# MunchkinDB recovery

This workspace preserves recoverable data from the former `munchkindb.ru` site.
The priority is evidence and structured data, not visual reconstruction.

## Current snapshot

The current archive pass contains 50,155 Common Crawl and Wayback index records.
It reconstructs 4,049 canonical cards: 3,787 full card pages and 262 cards known
only from archived set listings. It also contains 129 sets and all 59 non-spam
ruling records found in the archive inventories: 54 substantive explanations
and five original link-only pointers to recovered FAQ pages. See `recovery_report.md` for the
full evidence-backed status and remaining gaps.

## Static rules site

The repository includes the generated `site/`, maintained sources in `site_src/`,
build and recovery scripts, and JSON/CSV exports. Building and validating the
website requires Python 3.12 or newer and no third-party packages.

Recovery evidence (`raw/`, `data/`, and `munchkindb.sqlite`) is excluded from Git.
Keep a separate backup of these files if further archive recovery is planned.
Archive re-parsing, `validate_recovery.py`, and `generate_report.py` require
those local files; normal site builds and `validate_site.py` do not.

The `site/` directory is a generated, dependency-free rules reference suitable
for local browsing and static hosting. It contains individual pages for cards,
sets, and FAQ entries, plus a client-side search index. Card images are not
included in the public-ready build.

The home page at `/` contains archive statistics, search, and folder-shaped links
to sets, cards, and FAQ. The sets catalog is available at `/sets/`.
The top navigation links to `/contacts/` for feedback by email.

External proof URLs and strike-through/emphasis markup are restored at their
original character offsets from `exports/text_annotations.json`. This export
is tied to exact text hashes and archived captures. A stale annotation causes
the build to fail rather than attach a source to a different statement. Plain
references to printed card text/rules without a URL are explicitly labelled.

Question-and-answer sequences in general FAQs, recovered rules, and
card-specific FAQs are rendered as structured Q&A cards while preserving the
recovered wording and inline proof links.

The former separate rulings section is merged into seven archival FAQ groups.
An eighth group, Epic Munchkin, contains a sourced editorial introduction to the
historical home-play variant. It is not presented as currently supported
tournament play. Maintained Q&A and review metadata live in
`site_src/editorial_faq.json`; they do not overwrite recovered archive exports.
The Epic article is self-contained HTML, including classic-fantasy class/race
abilities and examples. It does not send readers to a PDF to understand play.
All historical `/rulings/` URLs remain as redirects, while card references and
search results go directly to the grouped FAQ pages. Five original link-only
records and one full duplicate point to their canonical FAQ pages.

The sets catalog follows the nested navigation recovered from the original
site. Empty set pages are omitted from the generated site, while known duplicate
historical slugs are merged into their canonical menu entries.
Russian titles for the numbered fantasy sets are maintained separately in
`site_src/set_localizations.json`, with publisher attribution. Unknown titles
and release years are not invented. Archive entry counts and physical card
counts from publisher lists are explicitly distinguished.

The card catalog is paginated into 100-entry static pages. Its search and the
notes-only filter cover all cards, not just the current page. Global search
includes alternative names, sets, tags and all note fields; exact titles rank
first. Queries and type filters are shareable through the URL. Results load
100 at a time while reporting the full match count. FAQ groups open when
addressed by a fragment; individual answers have permanent anchors.

Archive pages identify the capture date and explicitly avoid claiming a current
rule review. Feedback links include the affected page in an email draft.
Public URLs, canonicals and the sitemap use `site_src/site_config.json`.
See `deploy/README.md` for optional Nginx/Apache compression, caching and 404
configuration. No hosting settings or external deployment are changed by a build.

The original fantasy Munchkin family is additionally numbered from the base set
through Munchkin 10. This maintained layer keeps the archived exports immutable,
adds public source links to every numbered set, and adds newer releases missing
from the former site. Munchkin 9 includes the complete open card list published
by Steve Jackson Games. Munchkin 10 is intentionally metadata-only because its
official page confirms 112 cards but does not publish a complete card list.

Refresh the current numbered catalog and preserve the official source payloads:

```bash
python3 scripts/sync_munchkin_numbered_sets.py
```

The script writes `exports/munchkin_numbered_sets.json` and checksum sidecars
under `raw/external/official/`. Munchkin Wiki is retained as a community catalog
source through Munchkin 9; Munchkin 10 is sourced directly from the publisher.

Recover the latest most-complete archived set navigation after adding raw HTML:

```bash
python3 scripts/export_set_menu.py
```

Rebuild the site from the current JSON exports:

```bash
python3 scripts/build_site.py
```

Validate every generated page, search record, and internal link:

```bash
python3 scripts/validate_site.py
```

Content regression tests (no third-party Python dependencies):

```bash
python3 -m unittest discover -s tests -v
```

Browser tests require Firefox and geckodriver on `PATH`. They create a temporary
localhost server and browser session, test responsive layouts and search/FAQ
interactions, and stop both processes on completion:

```bash
python3 scripts/test_browser.py
```

GitHub Actions builds and validates with Python 3.12 and 3.13 on push/PR, and
runs browser tests on 3.12. It does not publish or push changes.

After re-parsing the archive, refresh annotations from the selected captures
using the local `raw/` and `munchkindb.sqlite`, then rebuild:

```bash
python3 scripts/export_text_annotations.py
```

External source availability is checked separately from the offline build:

```bash
python3 scripts/check_external_links.py
python3 scripts/build_site.py
python3 scripts/validate_site.py
```

The checker makes paced, bounded GET requests, resumes from
`exports/external_link_status.json`, and distinguishes missing pages, access
blocks and connection failures. Use `--limit 20` for a small batch or `--refresh`
to recheck existing entries. After three access refusals from the same host,
remaining URLs on that host are recorded as not individually checked, avoiding
repeated requests to a blocking server. An HTTP success is not verification of a rule;
403/429 and timeouts do not prove a page has disappeared. Failed checks have
dated UI notes and archive-search links, without replacing the original URL or
claiming an archive copy exists. Archive capture links themselves are excluded
from the automated sweep. Review the report before treating redirected URLs as
equivalent sources.

Preview it locally from the project root:

```bash
python3 -m http.server 8080 --directory site
```

Then open `http://127.0.0.1:8080/`. Do not open `site/index.html` directly:
root-relative links and the search-index request require an HTTP server.

The generator replaces the entire `site/` directory on every run. Maintained
frontend sources live under `site_src/`; do not edit generated HTML directly.
For deployment, upload the contents of `site/` as the document root of any
static host, enable HTTPS, and configure its custom 404 page as `404.html`.
Provider-specific configuration can be added after the hosting type and domain
are known.

## Current pipeline

1. Save every archive index response unchanged under `data/index/`.
2. Normalize HTTP/HTTPS and bare/`www` variants without deleting the originals.
3. Deduplicate captures by normalized URL, content digest, and archive source.
4. Export separate card, set, and other URL inventories under `data/inventory/`.

Run the Common Crawl inventory collector:

```bash
python3 scripts/build_inventory.py
```

Collect the complete paginated Wayback CDX domain inventory, then merge it:

```bash
python3 scripts/collect_wayback.py
python3 scripts/build_inventory.py --skip-download
```

Rebuild CSV/statistics from already downloaded index files:

```bash
python3 scripts/build_inventory.py --skip-download
```

Download and extract selected Common Crawl WARC response records:

```bash
python3 scripts/download_commoncrawl.py --url https://munchkindb.ru/card/warrior
```

Use bounded parallelism for a larger batch:

```bash
python3 scripts/download_commoncrawl.py --kind card --latest-per-url --workers 4 --delay 0.2
```

Both the exact compressed WARC record and its unmodified HTTP payload are saved
under `raw/commoncrawl/`. A JSON sidecar records the archive coordinates, response
headers, checksums, and output paths.

Extract internal pages and assets referenced by downloaded HTML:

```bash
python3 scripts/extract_links.py
```

The output keeps link text and the source page/timestamp so index pages can be used
as evidence for URLs whose own captures are missing.

Parse downloaded Drupal card, set, and ruling pages into SQLite and JSON:

```bash
python3 scripts/parse_archive.py
```

The database keeps every downloaded version in `archived_document` and versioned
content tables. `exports/card_pages.json`, `sets.json`, and `rulings.json` select
the newest recovered page version. `exports/cards.json` is the canonical union of
full card pages and partial cards known only from set listings; `recovery_status`
distinguishes those cases.
Standalone Drupal body pages (including FAQ pages) are exported to `pages.json`,
and deduplicated user comments to `comments.json`. Flat CSV exports are written
alongside the JSON files.

Fill page-path gaps from the latest available Wayback raw replay:

```bash
python3 scripts/download_wayback.py --kind card --latest-per-path --missing-from-commoncrawl
```

The gap mode compares URL paths (ignoring sort/query variants) against already
downloaded Common Crawl payloads. It does not remove older Wayback versions from
the inventory; those remain available for a later all-version pass.

Download the latest indexed copy of every non-HTML Wayback resource path
(images, CSS, JavaScript, feeds, JSON, and fonts):

```bash
python3 scripts/download_wayback.py --resources --latest-per-path --workers 2 --delay 0.5
```

The Wayback downloader chooses a filename extension from the replay Content-Type
and keeps the original indexed MIME type in the sidecar. An exact non-HTML URL,
such as `robots.txt`, can also be selected with a repeatable `--url` argument.

Classify downloaded non-HTML resources by domain era and implementation family:

```bash
python3 scripts/export_resources.py
```

This produces `data/inventory/resources.csv` and `exports/resource_stats.json`.
The classification is deliberately heuristic: raw evidence from unrelated legacy
content and post-2024 domain reuse is preserved, but it is not mixed into the
structured MunchkinDB catalog.

Run a reproducible comparison against a registered official card list:

```bash
python3 scripts/compare_official_set.py unnatural-axe
```

The current official HTML and checksum metadata are retained under
`raw/external/official/`; the normalized comparison is written under
`exports/official_comparison/`. Edition differences remain flagged for manual
review instead of being silently treated as missing data.

Regenerate the evidence-backed status report after parsing:

```bash
python3 scripts/generate_report.py
```

Verify every downloaded payload checksum and sidecar, parse all JSON exports,
run SQLite's integrity check, and confirm the handoff's control cards:

```bash
python3 scripts/validate_recovery.py
```

The machine-readable result is saved as `exports/validation.json` and is included
in the next generated recovery report.

The parser and validation stages use only Python's standard library. Network
collectors use `requests` where needed, send a descriptive User-Agent, retry
transient failures, and apply bounded request pacing. Common Crawl canonicalizes
the leading `www` for this domain; sampled `www` and bare-domain queries were
byte-identical, so the regular run sends only the bare-domain query while retaining
each record's original URL.

## Evidence policy

- Files in `data/index/` are raw source evidence and are not edited manually.
- Multiple timestamps and digests are retained.
- Empty index results are retained as empty files.
- `data/index/commoncrawl/errors.json` records incomplete queries.
- Normalization never replaces `original_url`.
