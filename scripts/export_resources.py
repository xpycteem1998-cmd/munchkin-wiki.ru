#!/usr/bin/env python3
"""Export a classified inventory of downloaded non-HTML resources."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = (
    ROOT / "data" / "inventory" / "download_manifest.jsonl",
    ROOT / "data" / "inventory" / "wayback_download_manifest.jsonl",
)
OUTPUT = ROOT / "data" / "inventory" / "resources.csv"
STATS = ROOT / "exports" / "resource_stats.json"

DRUPAL_PREFIXES = (
    "/misc/",
    "/modules/",
    "/sites/",
    "/taxonomy/term/",
    "/views/",
)
LEGACY_PREFIXES = (
    "//Upload_Books",
    "/BES/",
    "/SC/",
    "/The_big_Soviet_Encyclopedia/",
    "/Upload_Books",
    "/WL/",
    "/blocks/",
    "/images/",
    "/k-ser/",
    "/themes/Sunset/",
)
REUSE_PREFIXES = ("/cdn-cgi/", "/wp-content/", "/wp-includes/")


def classify(entry: dict[str, object]) -> str:
    url = str(entry.get("normalized_url", ""))
    parsed = urlsplit(url)
    path = parsed.path
    timestamp = str(entry.get("timestamp", ""))
    if path.startswith(REUSE_PREFIXES):
        return "post_2024_domain_reuse"
    if path.startswith(LEGACY_PREFIXES) or path == "/backend.php":
        return "unrelated_legacy_candidate"
    if (
        path.startswith(DRUPAL_PREFIXES)
        or path in ("/favicon.ico", "/robots.txt", "/rss.xml")
        or (path == "/index.php" and "admin%2Fviews%2Fajax" in parsed.query)
    ):
        return "drupal_munchkindb_candidate"
    if timestamp >= "20250000000000":
        return "post_2024_domain_reuse"
    return "unclassified"


def main() -> int:
    rows = []
    for manifest in MANIFESTS:
        with manifest.open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                entry = json.loads(line)
                if entry.get("mime") == "text/html":
                    continue
                parsed = urlsplit(str(entry.get("normalized_url", "")))
                rows.append(
                    {
                        "classification": classify(entry),
                        "source": entry.get("source", ""),
                        "normalized_url": entry.get("normalized_url", ""),
                        "path": parsed.path,
                        "timestamp": entry.get("timestamp", ""),
                        "status": entry.get("status", ""),
                        "mime": entry.get("mime", ""),
                        "digest": entry.get("digest", ""),
                        "payload_path": entry.get("payload_path", ""),
                        "payload_bytes": entry.get("payload_bytes", ""),
                        "payload_sha256": entry.get("payload_sha256", ""),
                    }
                )
    rows.sort(key=lambda row: (str(row["classification"]), str(row["path"]), str(row["timestamp"])))
    fieldnames = (
        "classification",
        "source",
        "normalized_url",
        "path",
        "timestamp",
        "status",
        "mime",
        "digest",
        "payload_path",
        "payload_bytes",
        "payload_sha256",
    )
    with OUTPUT.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    stats = {
        "classification_is_heuristic": True,
        "downloaded_non_html_captures": len(rows),
        "unique_resource_paths": len({str(row["path"]) for row in rows}),
        "by_classification": dict(sorted(Counter(str(row["classification"]) for row in rows).items())),
        "by_source": dict(sorted(Counter(str(row["source"]) for row in rows).items())),
        "by_mime": dict(sorted(Counter(str(row["mime"]) for row in rows).items())),
    }
    STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
