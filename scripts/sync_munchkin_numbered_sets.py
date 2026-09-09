#!/usr/bin/env python3
"""Refresh the numbered classic-Munchkin catalog from public sources."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

from compare_official_set import OfficialCardListParser


ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "raw" / "external" / "official"
OUTPUT = ROOT / "exports" / "munchkin_numbered_sets.json"
USER_AGENT = "munchkindb-recovery/0.1 (historical web preservation research)"
WIKI_URL = "https://munchkin.fandom.com/wiki/Munchkin_Wiki"

NUMBERED_SETS = (
    ("munchkin", None, "Munchkin", "https://www.munchkin.game/products/games/munchkin/"),
    ("unnatural-axe", "2", "Unnatural Axe", "https://www.munchkin.game/products/games/munchkin/munchkin-2-unnatural-axe/"),
    ("clerical-errors", "3", "Clerical Errors", "https://www.munchkin.game/products/games/munchkin/munchkin-3-clerical-errors/"),
    ("the-need-for-steed", "4", "The Need for Steed", "https://www.munchkin.game/products/games/munchkin/munchkin-4-the-need-for-steed/"),
    ("de-ranged", "5", "De-Ranged", "https://www.munchkin.game/products/games/munchkin/munchkin-5-de-ranged/"),
    ("demented-dungeons", "6", "Demented Dungeons", "https://www.munchkin.game/products/games/munchkin/munchkin-6-demented-dungeons/"),
    ("terrible-tombs", "6.5", "Terrible Tombs", "https://www.munchkin.game/products/games/munchkin/munchkin-6.5-terrible-tombs/"),
    ("cheat-with-both-hands", "7", "Cheat With Both Hands", "https://www.munchkin.game/products/games/munchkin/munchkin-7-cheat-with-both-hands/"),
    ("half-horse-will-travel", "8", "Half Horse, Will Travel", "https://www.munchkin.game/products/games/munchkin/munchkin-8-half-horse-will-travel/"),
    ("jurassic-snark", "9", "Munchkin 9 — Jurassic Snark", "https://www.munchkin.game/products/games/munchkin/munchkin-9-jurassic-snark/"),
    ("time-warp", "10", "Munchkin 10 — Time Warp", "https://munchkin.game/products/games/munchkin/munchkin-10-time-warp/"),
)

SUPPLEMENTAL_COUNTS = {"jurassic-snark": 112, "time-warp": 112}


def save_official_payload(
    session: requests.Session, slug: str, source_url: str, timestamp: str, retrieved_at: str
) -> tuple[bytes, str]:
    response = session.get(source_url, timeout=90)
    response.raise_for_status()
    payload = response.content
    html_path = RAW_ROOT / f"{slug}_{timestamp}.html"
    metadata_path = RAW_ROOT / f"{slug}_{timestamp}.json"
    html_path.write_bytes(payload)
    metadata = {
        "source_url": source_url,
        "retrieved_at": retrieved_at,
        "status": response.status_code,
        "final_url": response.url,
        "content_type": response.headers.get("content-type", ""),
        "payload_path": str(html_path.relative_to(ROOT)),
        "payload_bytes": len(payload),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload, str(metadata_path.relative_to(ROOT))


def main() -> int:
    now = datetime.now(timezone.utc)
    retrieved_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    RAW_ROOT.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    catalog: list[dict[str, object]] = []
    for slug, number, title, official_url in NUMBERED_SETS:
        sources = [
            {
                "title": f"Официальная страница — {title}",
                "url": official_url,
                "kind": "official",
            }
        ]
        # The community catalog currently enumerates the classic line through Munchkin 9.
        if slug != "time-warp":
            sources.append(
                {
                    "title": "Munchkin Wiki — каталог базовой серии",
                    "url": WIKI_URL,
                    "kind": "community",
                }
            )
        item: dict[str, object] = {
            "slug": slug,
            "series_number": number,
            "series_label": f"№ {number}" if number else "База",
            "title": title,
            "official_url": official_url,
            "sources": sources,
        }
        if slug in SUPPLEMENTAL_COUNTS:
            payload, metadata_path = save_official_payload(
                session, slug, official_url, timestamp, retrieved_at
            )
            parser = OfficialCardListParser()
            parser.feed(payload.decode("utf-8", errors="replace"))
            parsed_count = sum(parser.expected_counts.values())
            if parsed_count and parsed_count != len(parser.cards):
                raise RuntimeError(
                    f"{slug}: official headings declare {parsed_count} cards, "
                    f"but {len(parser.cards)} list entries were parsed"
                )
            expected_count = SUPPLEMENTAL_COUNTS[slug]
            if parsed_count and parsed_count != expected_count:
                raise RuntimeError(
                    f"{slug}: expected {expected_count} physical cards, official list has {parsed_count}"
                )
            if not re.search(
                rf"\b{expected_count}\s+cards?\b",
                payload.decode("utf-8", errors="ignore"),
                re.IGNORECASE,
            ):
                raise RuntimeError(f"{slug}: official page does not contain declared card count")
            item.update(
                {
                    "declared_card_count": expected_count,
                    "cards": [
                        {
                            "section": card["section"],
                            "title_en": card["name"],
                            "title_ru": "",
                        }
                        for card in parser.cards
                    ],
                    "coverage": "official_card_list" if parser.cards else "set_metadata_only",
                    "raw_official_metadata": metadata_path,
                }
            )
        catalog.append(item)

    result = {
        "generated_at": retrieved_at,
        "scope": "Numbered expansions for the original fantasy Munchkin line",
        "sets": catalog,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(OUTPUT.relative_to(ROOT)),
                "sets": len(catalog),
                "supplemental_sets": len(SUPPLEMENTAL_COUNTS),
                "open_card_list_entries": sum(len(item.get("cards", [])) for item in catalog),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
