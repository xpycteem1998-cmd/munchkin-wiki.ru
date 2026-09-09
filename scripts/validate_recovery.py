#!/usr/bin/env python3
"""Validate downloaded evidence and the generated recovery database/exports."""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = (
    ROOT / "data" / "inventory" / "download_manifest.jsonl",
    ROOT / "data" / "inventory" / "wayback_download_manifest.jsonl",
)
EXPECTED_EXPORTS = (
    "card_pages.json",
    "cards.json",
    "comments.json",
    "munchkin_numbered_sets.json",
    "pages.json",
    "rejected_pages.json",
    "rulings.json",
    "set_menu.json",
    "sets.json",
    "stats.json",
)
CONTROL_CARD_SLUGS = (
    "bat-bat",
    "chicken-steed",
    "elf",
    "ghoulfiends",
    "ranger",
    "tiny-hands",
    "undead-horse",
    "warrior",
)
EXCLUDED_RULING_SLUGS = {
    "instant-payday-loans-a-aid-in-problematic-period",
    "same-day-payday-cash-advances-for-poor-credit-dont-be-hesitated-now",
}
EXCLUDED_CARD_SLUGS = {
    "lloyd-lloigor&amp;ved=2ahukewjsuefw4ctqahxwgniehagmdpkqfjaregqiarab",
    "lloyd-lloigor&ved=2ahukewjsuefw4ctqahxwgniehagmdpkqfjaregqiarab",
}
EXPECTED_FAQ_ALIASES = {
    "faq-класс-раса-подданство-акцент": "/content/faq_class-race",
    "faq-монстры": "/content/faq_monsters",
    "faq-проклятия": "/faq_curses",
    "faq-разовые-шмотки": "/content/faq-по-разовым-шмоткам",
    "faq-шмотки": "/faq_items",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_number}: {error}") from error
    return entries


def normalized_path(url: str) -> str:
    return unquote(urlsplit(url).path).rstrip("/").casefold()


def inventory_slugs(filename: str, prefix: str) -> set[str]:
    slugs: set[str] = set()
    with (ROOT / "data" / "inventory" / filename).open(encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            path = unquote(urlsplit(str(row.get("normalized_url", ""))).path).rstrip("/")
            marker = f"/{prefix}/"
            if not path.startswith(marker):
                continue
            slug = path[len(marker):]
            if not slug or "/" in slug or Path(slug).suffix.lower() in {
                ".php", ".jpg", ".jpeg", ".png", ".gif", ".css", ".js", ".ico", ".svg"
            }:
                continue
            slugs.add(slug)
    return slugs


def main() -> int:
    failures: list[str] = []
    manifest_counts: dict[str, int] = {}
    checked_payloads = 0
    checked_external_payloads = 0

    for manifest_path in MANIFESTS:
        try:
            entries = load_manifest(manifest_path)
        except (OSError, ValueError) as error:
            failures.append(str(error))
            continue
        manifest_counts[manifest_path.name] = len(entries)
        for entry in entries:
            relative_payload = str(entry.get("payload_path", ""))
            payload_path = ROOT / relative_payload
            if not relative_payload or not payload_path.is_file():
                failures.append(f"missing payload: {relative_payload or '<empty>'}")
                continue
            checked_payloads += 1
            expected_hash = str(entry.get("payload_sha256", ""))
            actual_hash = sha256(payload_path)
            if not expected_hash or actual_hash != expected_hash:
                failures.append(f"payload checksum mismatch: {relative_payload}")

            sidecar_path = payload_path.with_suffix(".json")
            if not sidecar_path.is_file():
                failures.append(f"missing sidecar: {sidecar_path.relative_to(ROOT)}")
            else:
                try:
                    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as error:
                    failures.append(f"invalid sidecar {sidecar_path.relative_to(ROOT)}: {error}")
                else:
                    if sidecar.get("payload_sha256") != expected_hash:
                        failures.append(
                            f"sidecar checksum metadata mismatch: {sidecar_path.relative_to(ROOT)}"
                        )

            warc_relative = str(entry.get("warc_path", ""))
            if warc_relative and not (ROOT / warc_relative).is_file():
                failures.append(f"missing WARC record: {warc_relative}")

    for metadata_path in sorted((ROOT / "raw" / "external").glob("**/*.json")):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            payload_path = ROOT / str(metadata["payload_path"])
            expected_hash = str(metadata["payload_sha256"])
        except (OSError, json.JSONDecodeError, KeyError) as error:
            failures.append(f"invalid external metadata {metadata_path.relative_to(ROOT)}: {error}")
            continue
        if not payload_path.is_file():
            failures.append(f"missing external payload: {payload_path.relative_to(ROOT)}")
            continue
        checked_external_payloads += 1
        if sha256(payload_path) != expected_hash:
            failures.append(f"external payload checksum mismatch: {payload_path.relative_to(ROOT)}")

    for comparison_path in sorted((ROOT / "exports" / "official_comparison").glob("*.json")):
        try:
            json.loads(comparison_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            failures.append(f"invalid official comparison {comparison_path.relative_to(ROOT)}: {error}")

    parsed_exports: dict[str, int] = {}
    export_values: dict[str, object] = {}
    for export_name in EXPECTED_EXPORTS:
        export_path = ROOT / "exports" / export_name
        try:
            value = json.loads(export_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            failures.append(f"invalid export {export_name}: {error}")
            continue
        parsed_exports[export_name] = len(value) if isinstance(value, (list, dict)) else 1
        export_values[export_name] = value

    database_counts: dict[str, int | str] = {}
    set_menu = export_values.get("set_menu.json")
    if isinstance(set_menu, dict):
        def count_menu(items: list[dict[str, object]]) -> int:
            return sum(1 + count_menu(list(item.get("children", []))) for item in items)

        groups = set_menu.get("groups", [])
        actual_menu_entries = count_menu(groups) if isinstance(groups, list) else 0
        if actual_menu_entries != set_menu.get("menu_entries"):
            failures.append(
                f"set menu entry count mismatch: declared={set_menu.get('menu_entries')}, actual={actual_menu_entries}"
            )
        if not isinstance(groups, list) or not groups:
            failures.append("set menu has no groups")
    numbered_catalog = export_values.get("munchkin_numbered_sets.json")
    if isinstance(numbered_catalog, dict):
        numbered_sets = numbered_catalog.get("sets", [])
        if not isinstance(numbered_sets, list) or len(numbered_sets) != 11:
            failures.append("numbered Munchkin catalog must contain base set plus expansions 2-10")
        else:
            supplemental = {str(item.get("slug")): item for item in numbered_sets}
            if len(supplemental.get("jurassic-snark", {}).get("cards", [])) != 112:
                failures.append("Jurassic Snark official card list is incomplete")
            if supplemental.get("time-warp", {}).get("cards"):
                failures.append("Time Warp must not contain an unverified card list")

    ruling_inventory: dict[str, object] = {}
    card_inventory: dict[str, object] = {}
    set_inventory: dict[str, object] = {}
    rulings_export = export_values.get("rulings.json")
    pages_export = export_values.get("pages.json")
    if isinstance(rulings_export, list) and isinstance(pages_export, list):
        try:
            candidate_slugs = inventory_slugs("captures_other.csv", "ruling")
        except OSError as error:
            candidate_slugs = set()
            failures.append(f"cannot audit ruling inventory: {error}")
        exported_slugs = {str(item.get("slug", "")) for item in rulings_export}
        empty_rulings = sorted(
            str(item.get("slug", ""))
            for item in rulings_export
            if not str(item.get("text", "")).strip() and not item.get("internal_refs")
        )
        empty_pages = sorted(
            str(item.get("page_key", ""))
            for item in pages_export
            if not str(item.get("text", "")).strip()
        )
        if empty_rulings:
            failures.append(f"empty ruling exports: {', '.join(empty_rulings)}")
        if empty_pages:
            failures.append(f"empty generic page exports: {', '.join(empty_pages)}")
        expected_slugs = candidate_slugs - EXCLUDED_RULING_SLUGS
        missing_slugs = sorted(expected_slugs - exported_slugs)
        unexpected_slugs = sorted(exported_slugs - expected_slugs)
        if missing_slugs:
            failures.append(f"indexed rulings missing from exports: {', '.join(missing_slugs)}")
        if unexpected_slugs:
            failures.append(f"exported rulings absent from archive inventory: {', '.join(unexpected_slugs)}")

        pages_by_path = {
            normalized_path(str(item.get("page_key", ""))) for item in pages_export
        }
        ruling_by_slug = {str(item.get("slug", "")): item for item in rulings_export}
        for slug, expected_target in EXPECTED_FAQ_ALIASES.items():
            item = ruling_by_slug.get(slug, {})
            refs = list(item.get("internal_refs", []))
            actual_target = normalized_path(str(refs[0].get("url", ""))) if len(refs) == 1 else ""
            if actual_target != expected_target:
                failures.append(f"FAQ alias target mismatch: {slug}")
            elif actual_target not in pages_by_path:
                failures.append(f"FAQ alias target was not recovered: {slug} -> {actual_target}")
        ruling_inventory = {
            "candidate_slugs": len(candidate_slugs),
            "excluded_spam_slugs": len(EXCLUDED_RULING_SLUGS),
            "exported_slugs": len(exported_slugs),
            "substantive_rulings": len(exported_slugs) - len(EXPECTED_FAQ_ALIASES),
            "faq_aliases": len(EXPECTED_FAQ_ALIASES),
            "empty_rulings": empty_rulings,
            "empty_pages": empty_pages,
            "missing_slugs": missing_slugs,
            "unexpected_slugs": unexpected_slugs,
        }

    cards_export = export_values.get("cards.json")
    card_pages_export = export_values.get("card_pages.json")
    sets_export = export_values.get("sets.json")
    try:
        candidate_cards = inventory_slugs("captures_card.csv", "card")
        candidate_sets = inventory_slugs("captures_set.csv", "set")
    except OSError as error:
        candidate_cards = set()
        candidate_sets = set()
        failures.append(f"cannot audit card/set inventory: {error}")
    if isinstance(cards_export, list) and isinstance(card_pages_export, list):
        canonical_by_slug = {str(item.get("slug", "")): item for item in cards_export}
        full_page_slugs = {str(item.get("slug", "")) for item in card_pages_export}
        valid_candidate_cards = {
            slug for slug in candidate_cards if slug.casefold() not in EXCLUDED_CARD_SLUGS
        }
        missing_cards = sorted(valid_candidate_cards - canonical_by_slug.keys())
        if missing_cards:
            failures.append(f"indexed cards missing from canonical export: {', '.join(missing_cards)}")
        set_only_candidates = sorted(valid_candidate_cards - full_page_slugs)
        invalid_set_only = [
            slug
            for slug in set_only_candidates
            if canonical_by_slug.get(slug, {}).get("recovery_status") != "set_listing_only"
        ]
        if invalid_set_only:
            failures.append(
                f"indexed cards without full pages are not preserved from sets: {', '.join(invalid_set_only)}"
            )
        card_inventory = {
            "candidate_slugs": len(candidate_cards),
            "excluded_malformed_slugs": len(candidate_cards - valid_candidate_cards),
            "full_pages": len(full_page_slugs),
            "set_listing_only_candidates": len(set_only_candidates),
            "canonical_cards": len(canonical_by_slug),
            "missing_slugs": missing_cards,
        }
    if isinstance(sets_export, list):
        exported_sets = {str(item.get("slug", "")) for item in sets_export}
        missing_sets = sorted(candidate_sets - exported_sets)
        unexpected_sets = sorted(exported_sets - candidate_sets)
        if missing_sets:
            failures.append(f"indexed sets missing from exports: {', '.join(missing_sets)}")
        if unexpected_sets:
            failures.append(f"exported sets absent from archive inventory: {', '.join(unexpected_sets)}")
        set_inventory = {
            "candidate_slugs": len(candidate_sets),
            "exported_slugs": len(exported_sets),
            "missing_slugs": missing_sets,
            "unexpected_slugs": unexpected_sets,
        }
    try:
        connection = sqlite3.connect(ROOT / "munchkindb.sqlite")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        database_counts["integrity_check"] = integrity
        if integrity != "ok":
            failures.append(f"SQLite integrity check: {integrity}")
        for label, query in (
            ("canonical_cards", "SELECT COUNT(*) FROM canonical_card"),
            (
                "full_card_pages",
                "SELECT COUNT(*) FROM canonical_card WHERE recovery_status = 'full_page'",
            ),
            ("sets", "SELECT COUNT(DISTINCT slug) FROM set_version"),
            ("rulings", "SELECT COUNT(DISTINCT slug) FROM ruling_version"),
            ("pages", "SELECT COUNT(DISTINCT page_key) FROM generic_page_version"),
            ("comments", "SELECT COUNT(DISTINCT comment_id) FROM comment_version"),
        ):
            database_counts[label] = connection.execute(query).fetchone()[0]
        expected_export_counts = {
            "cards.json": database_counts["canonical_cards"],
            "card_pages.json": database_counts["full_card_pages"],
            "sets.json": database_counts["sets"],
            "rulings.json": database_counts["rulings"],
            "pages.json": database_counts["pages"],
            "comments.json": database_counts["comments"],
        }
        for export_name, expected_count in expected_export_counts.items():
            if export_name in parsed_exports and parsed_exports[export_name] != expected_count:
                failures.append(
                    f"export/database count mismatch: {export_name}="
                    f"{parsed_exports[export_name]}, database={expected_count}"
                )
        for export_name in ("cards.json", "card_pages.json", "sets.json", "rulings.json", "pages.json"):
            value = export_values.get(export_name)
            if not isinstance(value, list):
                continue
            slugs = [item.get("slug") for item in value if isinstance(item, dict)]
            if len(slugs) != len(set(slugs)):
                failures.append(f"duplicate slugs in export: {export_name}")
        found_controls = {
            row[0]
            for row in connection.execute(
                f"SELECT slug FROM canonical_card WHERE recovery_status = 'full_page' "
                f"AND slug IN ({','.join('?' for _ in CONTROL_CARD_SLUGS)})",
                CONTROL_CARD_SLUGS,
            )
        }
        missing_controls = sorted(set(CONTROL_CARD_SLUGS) - found_controls)
        if missing_controls:
            failures.append(f"missing control cards: {', '.join(missing_controls)}")
        connection.close()
    except sqlite3.Error as error:
        failures.append(f"SQLite validation failed: {error}")

    result = {
        "status": "ok" if not failures else "failed",
        "manifest_entries": manifest_counts,
        "payload_checksums_verified": checked_payloads,
        "external_payload_checksums_verified": checked_external_payloads,
        "exports_parsed": parsed_exports,
        "database": database_counts,
        "control_cards_verified": len(CONTROL_CARD_SLUGS),
        "ruling_inventory": ruling_inventory,
        "card_inventory": card_inventory,
        "set_inventory": set_inventory,
        "failure_count": len(failures),
        "failures": failures,
    }
    (ROOT / "exports" / "validation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
