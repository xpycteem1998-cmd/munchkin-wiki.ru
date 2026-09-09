#!/usr/bin/env python3
"""Paced, resumable GET checks of external proof URLs (not rule verification)."""
import argparse
import datetime
import ipaddress
import json
import socket
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]


def safe(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("not a public HTTP URL")
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(entry[4][0]).is_global for entry in addresses):
        raise ValueError("non-public destination")


class PublicRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = set()
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        value = attrs.get("href", "")
        parsed = urlsplit(value)
        if tag == "a" and parsed.scheme in ("https", "http") and parsed.hostname not in ("web.archive.org", "munchkin-wiki.ru"):
            self.urls.add(urlunsplit(parsed._replace(fragment="")))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, default=ROOT / "site")
    parser.add_argument("--output", type=Path, default=ROOT / "exports/external_link_status.json")
    parser.add_argument("--limit", type=int, default=0, help="0 checks all pending URLs")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    links = Links()
    for path in args.site.rglob("*.html"): links.feed(path.read_text())
    report = json.loads(args.output.read_text()) if args.output.exists() else {}
    blocked_hosts = {}
    if not args.refresh:
        for url, result in report.items():
            if result.get("status") == "blocked":
                host = urlsplit(url).hostname
                blocked_hosts[host] = blocked_hosts.get(host, 0) + 1
    opener = build_opener(PublicRedirect())
    pending = [url for url in sorted(links.urls) if args.refresh or url not in report]
    if args.limit: pending = pending[:args.limit]
    socket.setdefaulttimeout(6)
    for n, url in enumerate(pending, 1):
        result = {"checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
        host = urlsplit(url).hostname
        if blocked_hosts.get(host, 0) >= 3:
            result.update(status="not_checked_host_blocked", reason="Repeated access refusals from this host; no request sent")
            report[url] = result
            continue
        try:
            safe(url)
            with opener.open(Request(url, headers={"User-Agent": "MunchkinWiki-LinkCheck/1.0", "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.5"}), timeout=6) as response:
                sample = response.read(8192).lower()
                blocked = any(token in sample for token in (b"cf-chl-", b"just a moment...", b"verify you are human", b"access denied"))
                result.update(status="blocked" if blocked else "reachable", http_status=response.status, final_url=response.url)
        except HTTPError as exc:
            result.update(status="not_found" if exc.code in (404, 410) else "blocked" if exc.code in (401, 403, 429) else "http_error", http_status=exc.code)
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            result.update(status="network_error", error=type(exc).__name__)
        report[url] = result
        if result["status"] == "blocked": blocked_hosts[host] = blocked_hosts.get(host, 0) + 1
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(args.output)
        if n % 25 == 0: print(f"Checked {n}/{len(pending)}", flush=True)
        time.sleep(.3)
    from collections import Counter
    temporary = args.output.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(args.output)
    print(json.dumps({"unique_targets": len(links.urls), "checked": len(report), "statuses": dict(Counter(row['status'] for row in report.values()))}, ensure_ascii=False), flush=True)


if __name__ == "__main__": main()
