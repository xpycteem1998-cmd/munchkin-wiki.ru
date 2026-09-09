#!/usr/bin/env python3
"""Download and compare an official Munchkin card list with a recovered set."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "raw" / "external" / "official"
EXPORT_ROOT = ROOT / "exports" / "official_comparison"
USER_AGENT = "munchkindb-recovery/0.1 (historical web preservation research)"
KNOWN_SETS = {
    "unnatural-axe": "https://munchkin.game/products/games/munchkin/munchkin-2-unnatural-axe/",
}


class OfficialCardListParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.section: str | None = None
        self.expected_counts: dict[str, int] = {}
        self.cards: list[dict[str, str]] = []
        self._heading_depth = 0
        self._heading_parts: list[str] = []
        self._list_depth = 0
        self._list_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "h3":
            self._heading_depth = 1
            self._heading_parts = []
        elif self._heading_depth:
            self._heading_depth += 1
        if tag == "li" and self.section and not self._list_depth:
            self._list_depth = 1
            self._list_parts = []
        elif self._list_depth:
            self._list_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._heading_depth:
            self._heading_depth -= 1
            if tag == "h3" and self._heading_depth == 0:
                heading = " ".join(self._heading_parts).strip()
                match = re.fullmatch(r"(Doors|Treasures)\s*\((\d+)\)", heading, re.I)
                if match:
                    self.section = match.group(1).title()
                    self.expected_counts[self.section] = int(match.group(2))
                else:
                    self.section = None
        if self._list_depth:
            self._list_depth -= 1
            if tag == "li" and self._list_depth == 0 and self.section:
                name = re.sub(r"\s+", " ", " ".join(self._list_parts)).strip()
                if name:
                    self.cards.append({"section": self.section, "name": name})

    def handle_data(self, data: str) -> None:
        if self._heading_depth and data.strip():
            self._heading_parts.append(data.strip())
        if self._list_depth and data.strip():
            self._list_parts.append(data.strip())


def normalized_name(value: str) -> str:
    value = html.unescape(value).casefold()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(character for character in value if not unicodedata.combining(character))
    value = re.sub(r"^curse!\s*", "", value)
    value = re.sub(r"\s*\(gual\)\s*$", "", value)
    value = value.replace("your shoe's untied", "your shoes untied")
    value = value.replace("lose 1 ", "lose a ")
    return "".join(character for character in value if character.isalnum())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("set_slug", choices=tuple(KNOWN_SETS))
    parser.add_argument("--url", help="override the registered official URL")
    args = parser.parse_args()
    source_url = args.url or KNOWN_SETS[args.set_slug]

    response = requests.get(source_url, timeout=90, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    payload = response.content
    retrieved_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    RAW_ROOT.mkdir(parents=True, exist_ok=True)
    EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
    html_path = RAW_ROOT / f"{args.set_slug}_{timestamp}.html"
    metadata_path = RAW_ROOT / f"{args.set_slug}_{timestamp}.json"
    html_path.write_bytes(payload)
    metadata = {
        "source_url": source_url,
        "retrieved_at": retrieved_at,
        "status": response.status_code,
        "content_type": response.headers.get("content-type", ""),
        "payload_path": str(html_path.relative_to(ROOT)),
        "payload_bytes": len(payload),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    official_parser = OfficialCardListParser()
    official_parser.feed(payload.decode(response.encoding or "utf-8", errors="replace"))
    physical_count = sum(official_parser.expected_counts.values())
    if physical_count != len(official_parser.cards):
        raise RuntimeError(
            f"official parser count mismatch: headings={physical_count}, parsed={len(official_parser.cards)}"
        )

    recovered_sets = json.loads((ROOT / "exports" / "sets.json").read_text(encoding="utf-8"))
    recovered_set = next((item for item in recovered_sets if item["slug"] == args.set_slug), None)
    if recovered_set is None:
        raise RuntimeError(f"recovered set not found: {args.set_slug}")

    official_names = [item["name"] for item in official_parser.cards]
    official_nonblank = [name for name in official_names if normalized_name(name) != "blank"]
    official_by_key: dict[str, str] = {}
    for name in official_nonblank:
        official_by_key.setdefault(normalized_name(name), name)
    recovered_by_key = {
        normalized_name(str(card["title_en"])): {
            "slug": card["slug"],
            "title_en": card["title_en"],
            "title_ru": card["title_ru"],
            "section": card["section"],
        }
        for card in recovered_set["cards"]
    }
    missing = [official_by_key[key] for key in sorted(set(official_by_key) - set(recovered_by_key))]
    extra = [recovered_by_key[key] for key in sorted(set(recovered_by_key) - set(official_by_key))]
    duplicate_names = {
        name: count for name, count in sorted(Counter(official_names).items()) if count > 1
    }
    result = {
        "set_slug": args.set_slug,
        "recovered_set_title": recovered_set["title"],
        "official_source_url": source_url,
        "retrieved_at": retrieved_at,
        "official_expected_by_section": official_parser.expected_counts,
        "official_physical_cards": len(official_names),
        "official_blank_cards": sum(normalized_name(name) == "blank" for name in official_names),
        "official_unique_named_cards": len(official_by_key),
        "official_duplicate_names": duplicate_names,
        "recovered_unique_named_cards": len(recovered_by_key),
        "matched_unique_names": len(set(official_by_key) & set(recovered_by_key)),
        "missing_from_recovered_by_normalized_name": missing,
        "extra_in_recovered_by_normalized_name": extra,
        "comparison_note": "Current official edition and archived MunchkinDB may represent different printings; unmatched names require manual review.",
        "raw_official_metadata": str(metadata_path.relative_to(ROOT)),
    }
    output_path = EXPORT_ROOT / f"{args.set_slug}.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
