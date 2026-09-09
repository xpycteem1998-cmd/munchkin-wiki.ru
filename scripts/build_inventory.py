#!/usr/bin/env python3
"""Build a reproducible URL/capture inventory for munchkindb.ru archives."""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

import requests


ROOT = Path(__file__).resolve().parents[1]
CC_DIR = ROOT / "data" / "index" / "commoncrawl"
WB_DIR = ROOT / "data" / "index" / "wayback"
INVENTORY_DIR = ROOT / "data" / "inventory"
USER_AGENT = "munchkindb-recovery/0.1 (historical web preservation research)"
FIELDS = [
    "normalized_url",
    "original_url",
    "source",
    "collection",
    "timestamp",
    "status",
    "mime",
    "digest",
    "length",
    "offset",
    "filename",
    "urlkey",
]


def fetch(url: str, timeout: int = 90, attempts: int = 4) -> bytes:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            last_error = error
            if attempt + 1 < attempts:
                time.sleep((2**attempt) + random.random())
    assert last_error is not None
    raise last_error


def fetch_session(session: requests.Session, url: str, timeout: int = 90, attempts: int = 5) -> bytes:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response.content
        except requests.RequestException as error:
            last_error = error
            if isinstance(error, requests.HTTPError) and error.response is not None:
                status = error.response.status_code
                if status not in (408, 425, 429, 500, 502, 503, 504):
                    raise
            if attempt + 1 < attempts:
                time.sleep((3**attempt) + random.random())
    assert last_error is not None
    raise last_error


def normalize_url(raw_url: str) -> str:
    candidate = raw_url.strip()
    if "://" not in candidate:
        candidate = "http://" + candidate
    parsed = urlsplit(candidate)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host == "www.munchkindb.ru":
        host = "munchkindb.ru"
    port = parsed.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    path = quote(unquote(parsed.path or "/"), safe="/:@!$&'()*+,;=-._~")
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return urlunsplit(("https", netloc, path, query, ""))


def classify_url(normalized_url: str) -> str:
    path = urlsplit(normalized_url).path
    if path.startswith("/card/"):
        return "card"
    if path.startswith("/set/"):
        return "set"
    return "other"


def parse_json_lines(payload: bytes) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in payload.decode("utf-8", errors="replace").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def cc_query(
    session: requests.Session,
    collection: dict[str, Any],
    pattern: str,
    refresh: bool,
    delay: float,
) -> tuple[str, str, int, str | None]:
    collection_id = collection["id"]
    scope = "www" if pattern.startswith("www.") else "bare"
    target = CC_DIR / f"{collection_id}__{scope}.jsonl"
    if target.exists() and not refresh:
        return collection_id, scope, len(parse_json_lines(target.read_bytes())), None

    query = urlencode({"url": pattern, "output": "json"})
    url = f"{collection['cdx-api']}?{query}"
    try:
        payload = fetch_session(session, url)
        records = parse_json_lines(payload)
        target.write_bytes(payload)
        return collection_id, scope, len(records), None
    except requests.HTTPError as error:
        if error.response is not None and error.response.status_code == 404:
            target.write_bytes(b"")
            return collection_id, scope, 0, None
        status = error.response.status_code if error.response is not None else "unknown"
        return collection_id, scope, 0, f"HTTP {status}: {error}"
    except Exception as error:  # keep the remaining collections recoverable
        return collection_id, scope, 0, f"{type(error).__name__}: {error}"
    finally:
        time.sleep(delay)


def collect_commoncrawl(refresh: bool, delay: float) -> None:
    CC_DIR.mkdir(parents=True, exist_ok=True)
    collinfo_path = CC_DIR / "collinfo.json"
    if refresh or not collinfo_path.exists():
        collinfo_path.write_bytes(fetch("https://index.commoncrawl.org/collinfo.json"))
    collections = json.loads(collinfo_path.read_text(encoding="utf-8"))
    # Common Crawl canonicalizes away the leading www for this domain: sampled
    # bare and www queries returned byte-identical results. One query therefore
    # covers both original host variants without doubling public API traffic.
    jobs = [(collection, "munchkindb.ru/*") for collection in collections]
    errors: list[dict[str, str]] = []
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    for completed, (collection, pattern) in enumerate(jobs, 1):
        collection_id, scope, count, error = cc_query(session, collection, pattern, refresh, delay)
        if error:
            errors.append({"collection": collection_id, "scope": scope, "error": error})
        print(
            f"commoncrawl {completed}/{len(jobs)} {collection_id} {scope}: "
            f"{count} records" + (f" ERROR {error}" if error else ""),
            flush=True,
        )
    (CC_DIR / "errors.json").write_text(
        json.dumps(errors, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def load_commoncrawl() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted(CC_DIR.glob("CC-MAIN-*__bare.jsonl")):
        collection = path.name.split("__", 1)[0]
        for record in parse_json_lines(path.read_bytes()):
            original = str(record.get("url", ""))
            if not original:
                continue
            rows.append(
                {
                    "normalized_url": normalize_url(original),
                    "original_url": original,
                    "source": "commoncrawl",
                    "collection": collection,
                    "timestamp": str(record.get("timestamp", "")),
                    "status": str(record.get("status", "")),
                    "mime": str(record.get("mime", "")),
                    "digest": str(record.get("digest", "")),
                    "length": str(record.get("length", "")),
                    "offset": str(record.get("offset", "")),
                    "filename": str(record.get("filename", "")),
                    "urlkey": str(record.get("urlkey", "")),
                }
            )
    return rows


def load_wayback() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted(WB_DIR.glob("page-*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not data:
            continue
        header = data[0]
        for values in data[1:]:
            record = dict(zip(header, values))
            original = str(record.get("original", ""))
            if not original:
                continue
            rows.append(
                {
                    "normalized_url": normalize_url(original),
                    "original_url": original,
                    "source": "wayback",
                    "collection": "wayback",
                    "timestamp": str(record.get("timestamp", "")),
                    "status": str(record.get("statuscode", "")),
                    "mime": str(record.get("mimetype", "")),
                    "digest": str(record.get("digest", "")),
                    "length": str(record.get("length", "")),
                    "offset": "",
                    "filename": "",
                    "urlkey": str(record.get("urlkey", "")),
                }
            )
    return rows


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_inventory() -> dict[str, Any]:
    all_rows = load_commoncrawl() + load_wayback()
    all_rows.sort(
        key=lambda row: (
            row["normalized_url"],
            row["timestamp"],
            row["source"],
            row["collection"],
        )
    )
    unique_captures: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in all_rows:
        content_identity = row["digest"]
        if not content_identity or content_identity == "-":
            content_identity = "|".join((row["timestamp"], row["status"], row["mime"], row["length"]))
        key = (row["normalized_url"], content_identity, row["source"])
        unique_captures.setdefault(key, row)
    captures = list(unique_captures.values())
    captures.sort(key=lambda row: (row["normalized_url"], row["timestamp"]))

    by_kind = {kind: [] for kind in ("card", "set", "other")}
    for row in captures:
        by_kind[classify_url(row["normalized_url"])].append(row)

    write_csv(INVENTORY_DIR / "captures_all.csv", captures)
    for kind, rows in by_kind.items():
        write_csv(INVENTORY_DIR / f"captures_{kind}.csv", rows)

    logical_urls = sorted({row["normalized_url"] for row in captures})
    logical_paths = {urlsplit(url).path for url in logical_urls}
    url_kinds = Counter(classify_url(url) for url in logical_urls)
    path_kinds = Counter(classify_url(f"https://munchkindb.ru{path}") for path in logical_paths)

    def candidate_slugs(prefix: str) -> set[str]:
        marker = f"/{prefix}/"
        slugs = set()
        for path in logical_paths:
            if not path.startswith(marker):
                continue
            slug = path[len(marker) :]
            if not slug or "/" in slug or Path(slug).suffix.lower() in {
                ".php", ".jpg", ".jpeg", ".png", ".gif", ".css", ".js", ".ico", ".svg"
            }:
                continue
            slugs.add(slug)
        return slugs
    source_collections = sorted({row["collection"] for row in all_rows})
    successful_collections = sorted(
        {row["collection"] for row in all_rows if row["status"] == "200"}
    )
    errors_path = CC_DIR / "errors.json"
    errors = json.loads(errors_path.read_text(encoding="utf-8")) if errors_path.exists() else []
    stats = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "raw_index_records": len(all_rows),
        "unique_captures_by_url_digest_source": len(captures),
        "unique_normalized_urls": len(logical_urls),
        "unique_normalized_paths": len(logical_paths),
        "unique_card_urls": url_kinds["card"],
        "unique_set_urls": url_kinds["set"],
        "unique_other_urls": url_kinds["other"],
        "unique_card_paths": path_kinds["card"],
        "unique_set_paths": path_kinds["set"],
        "unique_other_paths": path_kinds["other"],
        "candidate_card_slugs": len(candidate_slugs("card")),
        "candidate_set_slugs": len(candidate_slugs("set")),
        "candidate_ruling_slugs": len(candidate_slugs("ruling")),
        "collections_with_records": len(source_collections),
        "collections_with_status_200": len(successful_collections),
        "query_errors": len(errors),
        "raw_records_by_source": dict(Counter(row["source"] for row in all_rows)),
        "unique_urls_by_source": {
            source: len({row["normalized_url"] for row in all_rows if row["source"] == source})
            for source in sorted({row["source"] for row in all_rows})
        },
        "unique_paths_by_source": {
            source: len({urlsplit(row["normalized_url"]).path for row in all_rows if row["source"] == source})
            for source in sorted({row["source"] for row in all_rows})
        },
        "status_counts": dict(Counter(row["status"] for row in all_rows)),
        "mime_counts": dict(Counter(row["mime"] for row in all_rows)),
    }
    (INVENTORY_DIR / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return stats


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="refetch existing index files")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds after each network query")
    parser.add_argument("--skip-download", action="store_true", help="only rebuild CSV/statistics")
    args = parser.parse_args()
    if args.delay < 0:
        parser.error("--delay must be non-negative")
    if not args.skip_download:
        collect_commoncrawl(args.refresh, args.delay)
    stats = build_inventory()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
