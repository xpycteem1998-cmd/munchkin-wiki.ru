#!/usr/bin/env python3
"""Download selected Wayback captures using raw (id_) replay URLs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import mimetypes
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import local
from typing import Any
from urllib.parse import urlsplit

import requests


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "data" / "inventory" / "captures_all.csv"
RAW_ROOT = ROOT / "raw" / "wayback"
COMMONCRAWL_ROOT = ROOT / "raw" / "commoncrawl"
MANIFEST = ROOT / "data" / "inventory" / "wayback_download_manifest.jsonl"
USER_AGENT = "munchkindb-recovery/0.1 (historical web preservation research)"
THREAD_STATE = local()


def thread_session() -> requests.Session:
    session = getattr(THREAD_STATE, "session", None)
    if session is None:
        session = requests.Session()
        session.headers["User-Agent"] = USER_AGENT
        THREAD_STATE.session = session
    return session


def existing_commoncrawl_paths() -> set[str]:
    paths = set()
    for metadata_path in COMMONCRAWL_ROOT.glob("*/*.json"):
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        paths.add(urlsplit(metadata["normalized_url"]).path)
    return paths


def load_rows(
    urls: list[str],
    kind: str | None,
    resources: bool,
    latest_per_path: bool,
    missing_from_commoncrawl: bool,
    limit: int | None,
) -> list[dict[str, str]]:
    with INVENTORY.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    rows = [row for row in rows if row["source"] == "wayback" and row["status"] == "200"]
    if urls:
        wanted = set(urls)
        rows = [row for row in rows if row["normalized_url"] in wanted]
    elif resources:
        rows = [row for row in rows if row["mime"] != "text/html"]
    else:
        rows = [row for row in rows if row["mime"] == "text/html"]
    if kind:
        marker = f"/{kind}/"
        rows = [row for row in rows if urlsplit(row["normalized_url"]).path.startswith(marker)]
    if latest_per_path:
        latest: dict[str, dict[str, str]] = {}
        for row in rows:
            path = urlsplit(row["normalized_url"]).path
            if path not in latest or row["timestamp"] > latest[path]["timestamp"]:
                latest[path] = row
        rows = list(latest.values())
    if missing_from_commoncrawl:
        existing = existing_commoncrawl_paths()
        rows = [row for row in rows if urlsplit(row["normalized_url"]).path not in existing]
    rows.sort(key=lambda row: (urlsplit(row["normalized_url"]).path, row["timestamp"]))
    if limit is not None:
        rows = rows[:limit]
    return rows


def output_stem(row: dict[str, str]) -> str:
    url_hash = hashlib.sha256(row["normalized_url"].encode()).hexdigest()[:12]
    return f"{row['digest']}_{row['timestamp']}_{url_hash}"


def extension_for(row: dict[str, str], response_content_type: str) -> str:
    content_type = (response_content_type or row.get("mime", "")).split(";", 1)[0].strip().lower()
    known = {
        "text/html": ".html",
        "text/plain": ".txt",
        "text/css": ".css",
        "application/javascript": ".js",
        "application/x-javascript": ".js",
        "text/javascript": ".js",
        # Keep payload JSON distinct from the metadata sidecar, which uses .json.
        "application/json": ".jsondata",
        "application/rss+xml": ".rss",
        "application/xml": ".xml",
        "text/xml": ".xml",
        "image/svg+xml": ".svg",
        "image/x-icon": ".ico",
        "application/font-woff": ".woff",
        "font/woff": ".woff",
        "font/woff2": ".woff2",
        "font/ttf": ".ttf",
        "application/vnd.ms-fontobject": ".eot",
    }
    return known.get(content_type) or mimetypes.guess_extension(content_type) or ".bin"


def fetch_capture(row: dict[str, str], refresh: bool, delay: float) -> tuple[dict[str, str], dict[str, Any]]:
    digest_dir = RAW_ROOT / row["digest"][:2]
    digest_dir.mkdir(parents=True, exist_ok=True)
    stem = output_stem(row)
    metadata_path = digest_dir / f"{stem}.json"
    if metadata_path.exists() and not refresh:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if (ROOT / str(metadata["payload_path"])).is_file():
            return row, metadata

    replay_url = f"https://web.archive.org/web/{row['timestamp']}id_/{row['original_url']}"
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            response = thread_session().get(replay_url, timeout=180, allow_redirects=True)
            response.raise_for_status()
            payload = response.content
            archived_content_length = response.headers.get("x-archive-orig-content-length", "")
            if not payload and archived_content_length != "0":
                raise RuntimeError("empty replay payload")
            payload_path = digest_dir / f"{stem}{extension_for(row, response.headers.get('content-type', ''))}"
            payload_path.write_bytes(payload)
            metadata = {
                **row,
                "replay_url": replay_url,
                "replay_final_url": response.url,
                "replay_status": response.status_code,
                "replay_content_type": response.headers.get("content-type", ""),
                "archived_content_length": archived_content_length,
                "payload_path": str(payload_path.relative_to(ROOT)),
                "payload_bytes": len(payload),
                "payload_sha256": hashlib.sha256(payload).hexdigest(),
            }
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            time.sleep(delay)
            return row, metadata
        except (requests.RequestException, RuntimeError) as error:
            last_error = error
            if isinstance(error, requests.HTTPError) and error.response is not None:
                if error.response.status_code not in (408, 425, 429, 500, 502, 503, 504):
                    raise
            if attempt < 4:
                time.sleep((3**attempt) + random.random())
    assert last_error is not None
    raise last_error


def rebuild_manifest() -> int:
    entries = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(RAW_ROOT.glob("*/*.json"))]
    entries.sort(key=lambda item: (item["normalized_url"], item["timestamp"], item["digest"]))
    MANIFEST.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in entries), encoding="utf-8"
    )
    return len(entries)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", action="append", default=[], help="exact normalized URL; repeatable")
    parser.add_argument("--kind", choices=("card", "set", "ruling"))
    parser.add_argument("--resources", action="store_true", help="select non-HTML resources")
    parser.add_argument("--latest-per-path", action="store_true")
    parser.add_argument("--missing-from-commoncrawl", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.workers < 1 or args.workers > 6:
        parser.error("--workers must be between 1 and 6")
    if args.kind and args.resources:
        parser.error("--kind and --resources are mutually exclusive")
    rows = load_rows(
        args.url,
        args.kind,
        args.resources,
        args.latest_per_path,
        args.missing_from_commoncrawl,
        args.limit,
    )
    completed = []
    failures = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_capture, row, args.refresh, args.delay): row for row in rows}
        for number, future in enumerate(as_completed(futures), 1):
            row = futures[future]
            try:
                _, metadata = future.result()
                completed.append(metadata)
                print(f"download {number}/{len(rows)} {row['normalized_url']} {row['timestamp']}", flush=True)
            except Exception as error:
                failures.append({"url": row["normalized_url"], "timestamp": row["timestamp"], "error": str(error)})
                print(f"ERROR {row['normalized_url']} {error}", file=sys.stderr, flush=True)
    manifest_entries = rebuild_manifest()
    summary = {
        "selected": len(rows),
        "completed_this_run": len(completed),
        "manifest_entries": manifest_entries,
        "failure_count": len(failures),
        "failures": failures,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
