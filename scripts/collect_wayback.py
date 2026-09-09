#!/usr/bin/env python3
"""Collect the complete paginated Wayback CDX domain inventory."""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import requests


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "data" / "index" / "wayback"
ENDPOINT = "https://web.archive.org/cdx/search/cdx"
USER_AGENT = "munchkindb-recovery/0.1 (historical web preservation research)"
BASE_PARAMS = {
    "url": "munchkindb.ru/",
    "matchType": "domain",
    "pageSize": "1",
}


def fetch(session: requests.Session, params: dict[str, str], attempts: int = 5) -> bytes:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = session.get(ENDPOINT, params=params, timeout=180)
            response.raise_for_status()
            return response.content
        except requests.RequestException as error:
            last_error = error
            if isinstance(error, requests.HTTPError) and error.response is not None:
                if error.response.status_code not in (408, 425, 429, 500, 502, 503, 504):
                    raise
            if attempt + 1 < attempts:
                time.sleep((3**attempt) + random.random())
    assert last_error is not None
    raise last_error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--delay", type=float, default=2.0)
    args = parser.parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    count_path = OUTPUT_DIR / "page_count.txt"
    if args.refresh or not count_path.exists():
        payload = fetch(session, {**BASE_PARAMS, "showNumPages": "true"})
        count_path.write_bytes(payload)
    page_count = int(count_path.read_text(encoding="utf-8").strip())
    errors = []
    for page in range(page_count):
        target = OUTPUT_DIR / f"page-{page:03d}.json"
        if target.exists() and not args.refresh:
            data = json.loads(target.read_text(encoding="utf-8"))
            print(f"wayback {page + 1}/{page_count}: {max(0, len(data) - 1)} cached records", flush=True)
            continue
        params = {
            **BASE_PARAMS,
            "page": str(page),
            "output": "json",
            "fl": "urlkey,timestamp,original,statuscode,mimetype,digest,length",
        }
        try:
            payload = fetch(session, params)
            data = json.loads(payload)
            target.write_bytes(payload)
            print(f"wayback {page + 1}/{page_count}: {max(0, len(data) - 1)} records", flush=True)
        except Exception as error:
            errors.append({"page": page, "error": f"{type(error).__name__}: {error}"})
            print(f"ERROR page {page}: {error}", file=sys.stderr, flush=True)
        time.sleep(args.delay)
    (OUTPUT_DIR / "errors.json").write_text(
        json.dumps(errors, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"pages": page_count, "errors": errors}, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
