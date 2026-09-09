#!/usr/bin/env python3
"""Validate the generated static site and every internal reference."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit
from site_content import ANNOTATIONS, EDITORIAL, SITE_URL

from build_site import (
    FAQ_GROUPS_FILE,
    PROOF_MARKER,
    build_ruling_redirects,
    faq_ruling_route,
    qa_pairs,
    validate_faq_groups,
)


ROOT = Path(__file__).resolve().parents[1]
EXPORTS = ROOT / "exports"
DEFAULT_SITE = ROOT / "site"


class ReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[str] = []
        self.has_main = False
        self.has_title = False
        self.inline_proofs = 0
        self.qa_items = 0
        self.faq_groups = 0
        self.ids = set()
        self.duplicate_ids = set()
        self.h1 = 0
        self.canonical = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            if values["id"] in self.ids: self.duplicate_ids.add(values["id"])
            self.ids.add(values["id"])
        if tag == "h1": self.h1 += 1
        if tag == "link" and values.get("rel") == "canonical": self.canonical.append(values.get("href", ""))
        if tag == "a" and "inline-proof" in str(values.get("class", "")).split():
            self.inline_proofs += 1
        if tag == "article" and "qa-item" in str(values.get("class", "")).split():
            self.qa_items += 1
        if tag == "details" and "faq-family" in str(values.get("class", "")).split():
            self.faq_groups += 1
        if tag == "main":
            self.has_main = True
        if tag == "title":
            self.has_title = True
        attribute = "href" if tag in {"a", "link"} else "src" if tag in {"script", "img"} else None
        if attribute and values.get(attribute):
            self.references.append(str(values[attribute]))


def target_for(site: Path, reference: str) -> Path | None:
    parsed = urlsplit(reference)
    if parsed.scheme or parsed.netloc or reference.startswith(("mailto:", "tel:", "#")):
        return None
    path = unquote(parsed.path)
    if not path:
        return None
    relative = path.lstrip("/")
    target = site / relative
    if path.endswith("/"):
        target /= "index.html"
    return target


def load_export(name: str) -> list[dict[str, object]]:
    return json.loads((EXPORTS / name).read_text(encoding="utf-8"))


def flattened_menu_slugs(items: list[dict[str, object]]) -> set[str]:
    output = set()
    for item in items:
        output.add(str(item["slug"]))
        output.update(flattened_menu_slugs(list(item.get("children", []))))
    return output


def expected_inline_proofs(rows: list[dict[str, object]], fields: tuple[str, ...]) -> int:
    expected = 0
    for row in rows:
        page = row.get("page") or row
        refs_by_field: dict[str, int] = defaultdict(int)
        for ref in page.get("inline_external_refs", []):
            if "пруф" in str(ref.get("title", "")).casefold():
                refs_by_field[str(ref.get("field", ""))] += 1
        for field in fields:
            markers = len(PROOF_MARKER.findall(str(page.get(field, ""))))
            expected += min(markers, refs_by_field[field])
    return expected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", type=Path, default=DEFAULT_SITE)
    args = parser.parse_args()
    site = args.site.resolve()
    errors: list[str] = []

    if not site.is_dir():
        parser.error(f"site directory does not exist: {site}")

    cards = load_export("cards.json")
    sets = load_export("sets.json")
    set_menu = json.loads((EXPORTS / "set_menu.json").read_text(encoding="utf-8"))
    numbered_catalog = json.loads(
        (EXPORTS / "munchkin_numbered_sets.json").read_text(encoding="utf-8")
    )
    rulings = load_export("rulings.json")
    pages = load_export("pages.json")
    ruling_redirects = build_ruling_redirects(rulings, pages)
    visible_rulings = [item for item in rulings if str(item["slug"]) not in ruling_redirects]
    visible_ruling_slugs = {str(item["slug"]) for item in visible_rulings}
    faq_pages = [item for item in pages if item.get("category") == "faq"]
    faq_groups = json.loads(FAQ_GROUPS_FILE.read_text(encoding="utf-8"))
    validate_faq_groups(faq_groups, visible_rulings, faq_pages)
    faq_groups.append(EDITORIAL["group"])
    build_info = json.loads((site / "build-info.json").read_text(encoding="utf-8"))
    search_index = json.loads((site / "data" / "search-index.json").read_text(encoding="utf-8"))
    html_files = sorted(site.rglob("*.html"))

    home_page = site / "index.html"
    home_text = home_page.read_text(encoding="utf-8") if home_page.is_file() else ""
    if 'class="metrics"' not in home_text or 'class="archive-folders"' not in home_text:
        errors.append("home page is missing archive statistics or folders")
    sets_index = site / "sets" / "index.html"
    if not sets_index.is_file() or '<h1>Наборы</h1>' not in sets_index.read_text(encoding="utf-8"):
        errors.append("sets catalog is missing")

    set_by_slug = {str(item["slug"]): item for item in sets}
    expected_set_slugs = {
        slug
        for slug in flattened_menu_slugs(list(set_menu.get("groups", [])))
        if slug in set_by_slug and set_by_slug[slug].get("cards")
    }
    catalog_sets = list(numbered_catalog.get("sets", []))
    supplemental_sets = [
        item
        for item in catalog_sets
        if str(item.get("slug", "")) not in set_by_slug
        and (item.get("declared_card_count") or item.get("cards"))
    ]
    expected_set_slugs.update(str(item["slug"]) for item in supplemental_sets)
    editorial_count = len(EDITORIAL["articles"])
    expected_search = len(cards) + len(expected_set_slugs) + len(visible_ruling_slugs) + len(pages) + editorial_count
    expected_html = expected_search + len(rulings) + 9 + (len(cards) + 99) // 100 - 1
    if len(search_index) != expected_search:
        errors.append(f"search records: expected {expected_search}, got {len(search_index)}")
    if len(html_files) != expected_html:
        errors.append(f"HTML pages: expected {expected_html}, got {len(html_files)}")
    if build_info.get("sets") != len(expected_set_slugs):
        errors.append(f"visible sets: expected {len(expected_set_slugs)}, got {build_info.get('sets')}")
    if build_info.get("rulings") != len(visible_ruling_slugs):
        errors.append(
            f"visible rulings: expected {len(visible_ruling_slugs)}, got {build_info.get('rulings')}"
        )
    if build_info.get("faq") != len(visible_ruling_slugs) + len(faq_pages) + editorial_count:
        errors.append(
            f"FAQ entries: expected {len(visible_ruling_slugs) + len(faq_pages) + editorial_count}, "
            f"got {build_info.get('faq')}"
        )
    if build_info.get("faq_groups") != len(faq_groups):
        errors.append(f"FAQ groups: expected {len(faq_groups)}, got {build_info.get('faq_groups')}")
    if build_info.get("source_rulings") != len(rulings):
        errors.append(f"source rulings: expected {len(rulings)}, got {build_info.get('source_rulings')}")
    if build_info.get("ruling_redirects") != len(ruling_redirects):
        errors.append(
            f"ruling redirects: expected {len(ruling_redirects)}, "
            f"got {build_info.get('ruling_redirects')}"
        )
    if build_info.get("legacy_ruling_redirects") != len(rulings):
        errors.append(
            f"legacy ruling redirects: expected {len(rulings)}, "
            f"got {build_info.get('legacy_ruling_redirects')}"
        )
    expected_numbered = {
        "munchkin": None,
        "unnatural-axe": "2",
        "clerical-errors": "3",
        "the-need-for-steed": "4",
        "de-ranged": "5",
        "demented-dungeons": "6",
        "terrible-tombs": "6.5",
        "cheat-with-both-hands": "7",
        "half-horse-will-travel": "8",
        "jurassic-snark": "9",
        "time-warp": "10",
    }
    actual_numbered = {
        str(item.get("slug", "")): item.get("series_number") for item in catalog_sets
    }
    if actual_numbered != expected_numbered:
        errors.append("numbered classic-Munchkin catalog is incomplete or incorrectly numbered")
    if build_info.get("current_numbered_sets") != len(expected_numbered):
        errors.append(
            f"numbered sets: expected {len(expected_numbered)}, "
            f"got {build_info.get('current_numbered_sets')}"
        )
    if build_info.get("supplemental_sets") != len(supplemental_sets):
        errors.append(
            f"supplemental sets: expected {len(supplemental_sets)}, "
            f"got {build_info.get('supplemental_sets')}"
        )
    for item in catalog_sets:
        sources = list(item.get("sources", []))
        if not any(source.get("kind") == "official" for source in sources):
            errors.append(f"numbered set has no official source: {item.get('slug')}")
    jurassic = next((item for item in catalog_sets if item.get("slug") == "jurassic-snark"), {})
    if jurassic.get("coverage") != "official_card_list" or len(jurassic.get("cards", [])) != 112:
        errors.append("Jurassic Snark must contain the complete 112-card official list")
    time_warp = next((item for item in catalog_sets if item.get("slug") == "time-warp"), {})
    if time_warp.get("coverage") != "set_metadata_only" or time_warp.get("cards"):
        errors.append("Time Warp must remain metadata-only until an open complete card list is found")
    empty_sets = [item for item in sets if not item.get("cards")]
    if build_info.get("empty_sets_removed") != len(empty_sets):
        errors.append(f"empty sets removed: expected {len(empty_sets)}, got {build_info.get('empty_sets_removed')}")
    for item in empty_sets:
        if (site / "sets" / str(item["slug"])).exists():
            errors.append(f"empty set page was generated: {item['slug']}")

    search_urls: set[str] = set()
    indexed_set_slugs: set[str] = set()
    indexed_faq_ruling_slugs: set[str] = set()
    for position, record in enumerate(search_index):
        for key in ("type", "title", "url", "search"):
            if not isinstance(record.get(key), str) or not record[key].strip():
                errors.append(f"search record {position}: missing {key}")
        url = str(record.get("url", ""))
        if url in search_urls:
            errors.append(f"duplicate search URL: {url}")
        search_urls.add(url)
        if record.get("type") == "Набор":
            indexed_set_slugs.add(unquote(urlsplit(url).path).strip("/").removeprefix("sets/"))
        path = unquote(urlsplit(url).path).strip("/")
        if record.get("type") == "FAQ" and path.startswith("faq/ruling--"):
            indexed_faq_ruling_slugs.add(path.removeprefix("faq/ruling--"))
        if path == "rulings" or path.startswith("rulings/"):
            errors.append(f"search record still uses the removed rulings section: {url}")
        target = target_for(site, url)
        if target is None or not target.is_file():
            errors.append(f"search URL does not resolve: {url}")
    if indexed_set_slugs != expected_set_slugs:
        errors.append("set search records do not match the archived menu plus current supplements")
    if indexed_faq_ruling_slugs != visible_ruling_slugs:
        errors.append("FAQ search records include aliases or omit migrated rulings")

    for item in rulings:
        slug = str(item["slug"])
        redirect = ruling_redirects.get(slug)
        target = str(redirect["target"]) if redirect else faq_ruling_route(slug)
        redirect_page = site / "rulings" / slug / "index.html"
        if not redirect_page.is_file():
            errors.append(f"ruling redirect page missing: {slug}")
            continue
        page_text = redirect_page.read_text(encoding="utf-8")
        if target not in page_text:
            errors.append(f"ruling redirect target missing from page: {slug}")
    ruling_index = site / "rulings" / "index.html"
    if not ruling_index.is_file() or "/faq/" not in ruling_index.read_text(encoding="utf-8"):
        errors.append("legacy rulings index does not redirect to FAQ")

    for item in catalog_sets:
        set_page = site / "sets" / str(item["slug"]) / "index.html"
        if not set_page.is_file():
            errors.append(f"numbered set page missing: {item['slug']}")
            continue
        page_text = set_page.read_text(encoding="utf-8")
        if str(item.get("series_label", "")) not in page_text:
            errors.append(f"number label missing from set page: {item['slug']}")
        for source in item.get("sources", []):
            if str(source.get("url", "")) not in page_text:
                errors.append(f"source link missing from set page: {item['slug']}")

    checked_references = 0
    inline_proofs = 0
    rendered_qa_items = 0
    rendered_faq_groups = 0
    documents = {}
    fragment_refs = []
    redirect_source_paths = {
        f"/rulings/{item['slug']}/" for item in rulings
    }
    redirect_source_paths.add("/rulings/")
    for html_file in html_files:
        document = ReferenceParser()
        try:
            document.feed(html_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            errors.append(f"cannot parse {html_file.relative_to(site)}: {exc}")
            continue
        if not document.has_title:
            errors.append(f"missing title: {html_file.relative_to(site)}")
        if not document.has_main:
            errors.append(f"missing main landmark: {html_file.relative_to(site)}")
        if document.h1 != 1 or document.duplicate_ids:
            errors.append(f"invalid headings/duplicate IDs: {html_file.relative_to(site)}")
        if html_file.name != "404.html" and (len(document.canonical) != 1 or not document.canonical[0].startswith(SITE_URL + "/")):
            errors.append(f"invalid canonical: {html_file.relative_to(site)}")
        documents[html_file.resolve()] = document
        inline_proofs += document.inline_proofs
        rendered_qa_items += document.qa_items
        rendered_faq_groups += document.faq_groups
        for reference in document.references:
            parsed_ref = urlsplit(reference)
            if parsed_ref.fragment and not parsed_ref.scheme and not parsed_ref.netloc:
                fragment_target = target_for(site, reference) if parsed_ref.path else html_file
                fragment_refs.append((fragment_target, unquote(parsed_ref.fragment), html_file))
            if unquote(urlsplit(reference).path) in redirect_source_paths:
                errors.append(
                    f"internal page links to a FAQ alias instead of its target in "
                    f"{html_file.relative_to(site)}: {reference}"
                )
            target = target_for(site, reference)
            if target is None:
                continue
            checked_references += 1
            try:
                target.relative_to(site)
            except ValueError:
                errors.append(f"reference escapes site root in {html_file.relative_to(site)}: {reference}")
                continue
            if not target.is_file():
                errors.append(f"broken reference in {html_file.relative_to(site)}: {reference}")

    for target, fragment, origin in fragment_refs:
        document = documents.get(target.resolve()) if target else None
        if not document or fragment not in document.ids:
            errors.append(f"broken fragment in {origin.relative_to(site)}: {fragment}")
    annotation_keys = ["card/" + str(card["slug"]) for card in cards if card.get("page")]
    annotation_keys += ["ruling/" + str(item["slug"]) for item in visible_rulings]
    annotation_keys += ["page/" + str(item["page_key"]) for item in pages]
    expected_proofs = sum(1 for key in annotation_keys for field in ANNOTATIONS[key]["fields"].values() for span in field["spans"] if span["tag"] == "a")
    expected_proofs += sum(len(item["qa"]) for item in EDITORIAL["articles"])
    if inline_proofs != expected_proofs:
        errors.append(f"inline proof links: expected {expected_proofs}, got {inline_proofs}")

    expected_qa_items = sum(
        len(qa_pairs(str((card.get("page") or {}).get("faq_text", "")))[1])
        for card in cards
    )
    expected_qa_items += sum(
        len(qa_pairs(str(item.get("text", "")))[1])
        for item in visible_rulings
    )
    expected_qa_items += sum(
        len(qa_pairs(str(item.get("text", "")))[1])
        for item in pages
        if item.get("category") == "faq"
    )
    expected_qa_items += sum(len(item["qa"]) for item in EDITORIAL["articles"])
    expected_qa_items += sum(1 for item in visible_rulings if str(item["slug"]) in EDITORIAL["qa_overrides"] and not qa_pairs(str(item.get("text", "")))[1])
    if rendered_qa_items != expected_qa_items:
        errors.append(
            f"Q&A blocks: expected {expected_qa_items}, got {rendered_qa_items}"
        )
    if rendered_faq_groups != len(faq_groups):
        errors.append(f"FAQ group sections: expected {len(faq_groups)}, got {rendered_faq_groups}")

    report = {
        "status": "ok" if not errors else "failed",
        "html_pages": len(html_files),
        "search_records": len(search_index),
        "visible_sets": len(expected_set_slugs),
        "faq_entries": len(visible_ruling_slugs) + len(faq_pages) + editorial_count,
        "faq_rulings": len(visible_ruling_slugs),
        "faq_groups": len(faq_groups),
        "ruling_redirects": len(ruling_redirects),
        "legacy_ruling_redirects": len(rulings),
        "numbered_sets": len(catalog_sets),
        "supplemental_sets": len(supplemental_sets),
        "empty_sets_removed": len(empty_sets),
        "internal_references_checked": checked_references,
        "inline_proof_links": inline_proofs,
        "qa_items": rendered_qa_items,
        "errors": errors[:100],
    }
    (site / "validation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
