#!/usr/bin/env python3
"""Download and extract selected Common Crawl WARC response records."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import mimetypes
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import local
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "data" / "inventory" / "captures_all.csv"
RAW_ROOT = ROOT / "raw" / "commoncrawl"
MANIFEST = ROOT / "data" / "inventory" / "download_manifest.jsonl"
USER_AGENT = "munchkindb-recovery/0.1 (historical web preservation research)"
THREAD_STATE = local()


def parse_headers(block: bytes) -> tuple[str, dict[str, str]]:
    lines = block.decode("iso-8859-1", errors="replace").splitlines()
    first = lines[0] if lines else ""
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    return first, headers


def extract_record(compressed: bytes) -> tuple[dict[str, Any], bytes]:
    record = gzip.decompress(compressed)
    warc_block, separator, remainder = record.partition(b"\r\n\r\n")
    if not separator:
        raise ValueError("WARC header separator not found")
    warc_status, warc_headers = parse_headers(warc_block)
    http_block, separator, payload = remainder.partition(b"\r\n\r\n")
    if not separator:
        raise ValueError("HTTP header separator not found")
    http_status, http_headers = parse_headers(http_block)
    metadata = {
        "warc_status": warc_status,
        "warc_headers": warc_headers,
        "http_status": http_status,
        "http_headers": http_headers,
        "record_uncompressed_bytes": len(record),
        "payload_bytes": len(payload),
    }
    return metadata, payload


def extension_for(row: dict[str, str], headers: dict[str, str]) -> str:
    content_type = headers.get("content-type", row.get("mime", "")).split(";", 1)[0].strip()
    known = {
        "text/html": ".html",
        "text/plain": ".txt",
        "application/json": ".json",
        "application/rss+xml": ".xml",
        "text/css": ".css",
        "application/javascript": ".js",
        "text/javascript": ".js",
    }
    return known.get(content_type) or mimetypes.guess_extension(content_type) or ".bin"


def load_rows(
    urls: list[str], kind: str | None, latest_per_url: bool, limit: int | None
) -> list[dict[str, str]]:
    with INVENTORY.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    rows = [row for row in rows if row["source"] == "commoncrawl" and row["status"] == "200"]
    if urls:
        wanted = set(urls)
        rows = [row for row in rows if row["normalized_url"] in wanted]
    if kind:
        marker = f"/{kind}/"
        rows = [row for row in rows if marker in row["normalized_url"]]
    rows.sort(key=lambda row: (row["normalized_url"], row["timestamp"]), reverse=False)
    if latest_per_url:
        latest: dict[str, dict[str, str]] = {}
        for row in rows:
            latest[row["normalized_url"]] = row
        rows = sorted(latest.values(), key=lambda row: row["normalized_url"])
    if limit is not None:
        rows = rows[:limit]
    return rows


def output_stem(row: dict[str, str]) -> str:
    url_hash = hashlib.sha256(row["normalized_url"].encode()).hexdigest()[:12]
    return f"{row['digest']}_{row['timestamp']}_{url_hash}"


def download_one(session: requests.Session, row: dict[str, str], refresh: bool) -> dict[str, Any]:
    digest_dir = RAW_ROOT / row["digest"][:2]
    digest_dir.mkdir(parents=True, exist_ok=True)
    stem = output_stem(row)
    warc_path = digest_dir / f"{stem}.warc.gz"
    metadata_path = digest_dir / f"{stem}.json"
    if warc_path.exists() and metadata_path.exists() and not refresh:
        return json.loads(metadata_path.read_text(encoding="utf-8"))

    offset = int(row["offset"])
    length = int(row["length"])
    endpoint = "https://data.commoncrawl.org/" + row["filename"]
    headers = {"Range": f"bytes={offset}-{offset + length - 1}", "Accept-Encoding": "identity"}
    response = session.get(endpoint, headers=headers, timeout=120)
    response.raise_for_status()
    if response.status_code != 206:
        raise RuntimeError(f"expected HTTP 206, received {response.status_code}")
    if len(response.content) != length:
        raise RuntimeError(f"expected {length} bytes, received {len(response.content)}")

    metadata, payload = extract_record(response.content)
    payload_path = digest_dir / f"{stem}{extension_for(row, metadata['http_headers'])}"
    warc_path.write_bytes(response.content)
    payload_path.write_bytes(payload)
    output = {
        **row,
        **metadata,
        "warc_path": str(warc_path.relative_to(ROOT)),
        "payload_path": str(payload_path.relative_to(ROOT)),
        "compressed_bytes": len(response.content),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }
    metadata_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def rebuild_manifest() -> int:
    entries = []
    for path in sorted(RAW_ROOT.glob("*/*.json")):
        entries.append(json.loads(path.read_text(encoding="utf-8")))
    entries.sort(key=lambda item: (item["normalized_url"], item["timestamp"], item["digest"]))
    MANIFEST.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in entries), encoding="utf-8"
    )
    return len(entries)


def thread_session() -> requests.Session:
    session = getattr(THREAD_STATE, "session", None)
    if session is None:
        session = requests.Session()
        session.headers["User-Agent"] = USER_AGENT
        THREAD_STATE.session = session
    return session


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", action="append", default=[], help="exact normalized URL; repeatable")
    parser.add_argument("--kind", choices=("card", "set", "ruling"))
    parser.add_argument("--latest-per-url", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.workers < 1 or args.workers > 8:
        parser.error("--workers must be between 1 and 8")
    rows = load_rows(args.url, args.kind, args.latest_per_url, args.limit)
    completed: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    def worker(row: dict[str, str]) -> tuple[dict[str, str], dict[str, Any]]:
        result = download_one(thread_session(), row, args.refresh)
        time.sleep(args.delay)
        return row, result

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(worker, row): row for row in rows}
        for number, future in enumerate(as_completed(futures), 1):
            row = futures[future]
            try:
                _, result = future.result()
                completed.append(result)
                print(f"download {number}/{len(rows)} {row['normalized_url']} {row['timestamp']}", flush=True)
            except Exception as error:
                failures.append({"url": row["normalized_url"], "timestamp": row["timestamp"], "error": str(error)})
                print(f"ERROR {row['normalized_url']} {error}", file=sys.stderr, flush=True)
    manifest_entries = rebuild_manifest()
    print(
        json.dumps(
            {
                "selected": len(rows),
                "completed_this_run": len(completed),
                "manifest_entries": manifest_entries,
                "failed": failures,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
