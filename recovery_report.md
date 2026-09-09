# MunchkinDB recovery report

Generated: 2026-09-09 21:51 UTC

## Current outcome

The archive inventory phase is complete for Wayback Machine and all published
Common Crawl collections. A reproducible raw-download and Drupal parser pipeline
is working, and the current SQLite/JSON/CSV exports contain both full card pages
and partial cards recovered from set listings. A dependency-free static site is
built from those exports for local use and conventional static hosting.

## Archive inventory

- Common Crawl: 127/127 collections queried; 7,631 raw index records; 1,386 normalized URLs.
- Wayback Machine: 8/8 domain-index pages downloaded; 42,524 raw CDX records; 10,840 normalized URLs.
- Arquivo.pt: exact URL history and CDX wildcard checks returned zero results.
- Combined: 50,155 raw records, 48,606 distinct source/URL/digest captures, 10,919 normalized URLs, and 6,814 URL paths.
- Candidate paths: 3,796 card slugs, 129 set slugs, and 61 ruling-like slugs. Two ruling-like paths are unrelated payday-loan spam and are excluded from structured data.

## Downloaded raw data

- Common Crawl WARC records/payloads: 1,324.
- Wayback raw-replay payloads: 3,270.
- Current raw directory size: 158.3 MiB.
- Every downloaded item has source coordinates, timestamp, digest, payload checksum, and local path in a JSON sidecar/manifest.
- Downloaded non-HTML captures: 348 across 346 paths.
- Resource classification is heuristic: 168 Drupal/MunchkinDB candidates, 63 unrelated legacy candidates, 116 post-2024 domain-reuse captures, and 1 unclassified.

## Structured recovery

- Canonical cards: 4,049.
- Full card pages: 3,787 (93.5%).
- Set-listing-only partial cards: 262.
- Cards with Russian title: 2,740 (67.7%).
- Sets: 129.
- Card-to-set relationships: 4,772.
- Ruling records: 59; substantive explanations: 54; original FAQ link aliases retained as redirects: 5.
- Generic content pages: 6; standalone FAQ pages: 5.
- Unique archived comments: 41.
- Cards with application notes, FAQ, facts, or errata: 389.
- External proof/source links: 832.
- Parser-rejected path/type mismatches: 81; raw evidence remains preserved.

## Validation

- Validation status: ok.
- Payload SHA-256 checksums verified: 4,594.
- SQLite integrity check: ok.
- Handoff control cards verified as full pages: 8.
- Card inventory audit: 3,796 candidate slugs, 3,787 full pages, 7 legitimate set-listing-only cards, 2 malformed archive URLs excluded, and 0 missing legitimate cards.
- Set inventory audit: 129 indexed slugs, 129 recovered sets, and 0 missing sets.
- Ruling inventory audit: 61 indexed paths, 59 recovered records, 2 excluded spam paths, and 0 missing legitimate paths.
- External official-source payloads verified: 5.

## Browsable site

- Generated HTML pages: 4,249.
- Search-index records: 4,181.
- Visible sets: 73 in 22 archived menu groups; 50 empty sets removed and 8 duplicate aliases merged.
- Site validation: ok; internal references checked: 57,862.
- Proof links embedded at their original text markers: 823.
- Question-and-answer pairs rendered as structured Q&A cards: 147.
- FAQ catalog: 58 materials in 7 thematic groups, including 53 migrated ruling pages.
- The separate rulings section is removed; all 59 historical detail URLs and its former index redirect into FAQ.
- Duplicate or link-only ruling records redirected to canonical FAQ pages: 6.
- The site is static and needs no application server or database at runtime.
- Card images are intentionally excluded from the public-ready build pending a separate rights review.
- Current numbered classic-Munchkin catalog entries: 11; newer supplements with a full official card list: 1; metadata-only newer supplements: 1.

## Official catalog cross-check

- Unnatural Axe official physical cards: 112; unique named cards: 102.
- Recovered unique named cards: 102; normalized name matches: 102.
- Missing official names: 0; extra recovered names: 0.
- This comparison is against the current official edition; printing differences remain a manual-review boundary.

## Outputs

- `munchkindb.sqlite`: versioned documents, card/set/ruling tables, canonical cards, and relationships.
- `exports/cards.json` and `exports/cards.csv`: full and partial canonical card catalog with recovery status.
- `exports/card_pages.json`: latest parsed full page for every recovered card page.
- `exports/sets.json`, `rulings.json`, `pages.json`, and `comments.json`, with matching CSV exports.
- `exports/munchkin_numbered_sets.json`: maintained numbering, open source links, and post-archive additions for the original Munchkin line.
- `data/inventory/captures_*.csv`: complete merged capture inventory; `resources.csv` classifies downloaded non-HTML evidence.
- `raw/`: exact Common Crawl WARC records, extracted payloads, and Wayback raw replay payloads.
- `site/`: generated static website; `site_src/` contains its maintained CSS and JavaScript sources.

## Remaining work

1. Download all distinct historical page digests, not only the newest usable page per path.
2. Download historical versions of assets and recover referenced resources absent from archive indexes.
3. Mine search engines, backlinks, forums, and PDFs for card/ruling text absent from both main archives.
4. Compare each recovered set against official Steve Jackson Games card lists and quantify external completeness.
5. Choose the hosting provider and domain, then add provider-specific deployment configuration and publish only after explicit approval.

The current percentages measure recovery against the catalog reconstructed from
MunchkinDB's own archived set pages. They are not yet a claim of completeness
against every official Munchkin release.
