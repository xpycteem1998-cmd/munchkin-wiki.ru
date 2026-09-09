#!/usr/bin/env python3
"""Extract internal URLs and backlink context from downloaded HTML payloads."""

from __future__ import annotations

import csv
import html
import json
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
RAW_ROOTS = (ROOT / "raw" / "commoncrawl", ROOT / "raw" / "wayback")
OUTPUT = ROOT / "data" / "inventory" / "discovered_links.csv"
STATS = ROOT / "data" / "inventory" / "discovered_links_stats.json"


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self._anchor: dict[str, str] | None = None
        self._anchor_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value or "" for name, value in attrs}
        if tag == "a" and values.get("href"):
            self._anchor = {"href": values["href"], "relation": "anchor"}
            self._anchor_text = []
        elif tag in ("img", "script") and values.get("src"):
            self.links.append({"href": values["src"], "relation": tag, "anchor_text": values.get("alt", "")})
        elif tag == "link" and values.get("href"):
            self.links.append({"href": values["href"], "relation": values.get("rel", "link"), "anchor_text": ""})

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._anchor is not None:
            self._anchor["anchor_text"] = re.sub(r"\s+", " ", " ".join(self._anchor_text)).strip()
            self.links.append(self._anchor)
            self._anchor = None
            self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if self._anchor is not None and data.strip():
            self._anchor_text.append(data.strip())


def normalize(raw_url: str, base_url: str) -> tuple[str, str] | None:
    raw_url = html.unescape(raw_url).strip()
    if not raw_url or raw_url.startswith(("#", "mailto:", "javascript:", "data:")):
        return None
    absolute = urljoin(base_url, raw_url)
    parsed = urlsplit(absolute)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in ("munchkindb.ru", "www.munchkindb.ru"):
        return None
    path = quote(unquote(parsed.path or "/"), safe="/:@!$&'()*+,;=-._~")
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    normalized = urlunsplit(("https", "munchkindb.ru", path, query, ""))
    return absolute, normalized


def classify(url: str) -> str:
    path = urlsplit(url).path
    for kind in ("card", "set", "ruling", "content", "tag"):
        if path.startswith(f"/{kind}/"):
            return kind
    suffix = Path(path).suffix.lower()
    if suffix in (".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico"):
        return "asset"
    return "other"


def main() -> int:
    rows: list[dict[str, str]] = []
    metadata_paths = [path for raw_root in RAW_ROOTS for path in raw_root.glob("*/*.json")]
    for metadata_path in sorted(metadata_paths):
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        payload_path = ROOT / metadata["payload_path"]
        if payload_path.suffix.lower() not in (".html", ".htm"):
            continue
        parser = LinkParser()
        parser.feed(payload_path.read_text(encoding="utf-8", errors="replace"))
        for link in parser.links:
            result = normalize(link["href"], metadata["original_url"])
            if result is None:
                continue
            absolute, normalized = result
            rows.append(
                {
                    "normalized_url": normalized,
                    "url_type": classify(normalized),
                    "original_url": absolute,
                    "relation": link["relation"],
                    "anchor_text": link.get("anchor_text", ""),
                    "source_page": metadata["normalized_url"],
                    "source_timestamp": metadata["timestamp"],
                    "source_payload": metadata["payload_path"],
                }
            )
    unique = {
        (row["normalized_url"], row["source_page"], row["source_timestamp"], row["relation"], row["anchor_text"]): row
        for row in rows
    }
    output_rows = sorted(
        unique.values(), key=lambda row: (row["normalized_url"], row["source_page"], row["source_timestamp"])
    )
    fieldnames = [
        "normalized_url",
        "url_type",
        "original_url",
        "relation",
        "anchor_text",
        "source_page",
        "source_timestamp",
        "source_payload",
    ]
    with OUTPUT.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)
    logical_urls = {row["normalized_url"] for row in output_rows}
    counts = Counter(classify(url) for url in logical_urls)
    stats = {
        "html_payloads_scanned": sum(len(list(raw_root.glob("*/*.html"))) for raw_root in RAW_ROOTS),
        "link_occurrences": len(rows),
        "unique_contextual_links": len(output_rows),
        "unique_internal_urls": len(logical_urls),
        "unique_urls_by_type": dict(sorted(counts.items())),
    }
    STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
