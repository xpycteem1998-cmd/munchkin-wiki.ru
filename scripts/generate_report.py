#!/usr/bin/env python3
"""Generate a concise, evidence-backed recovery status report."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_STATS = ROOT / "data" / "inventory" / "stats.json"
PARSE_STATS = ROOT / "exports" / "stats.json"
VALIDATION = ROOT / "exports" / "validation.json"
RESOURCE_STATS = ROOT / "exports" / "resource_stats.json"
OFFICIAL_UNNATURAL_AXE = ROOT / "exports" / "official_comparison" / "unnatural-axe.json"
SITE_BUILD = ROOT / "site" / "build-info.json"
SITE_VALIDATION = ROOT / "site" / "validation.json"
NUMBERED_SETS = ROOT / "exports" / "munchkin_numbered_sets.json"
DB_PATH = ROOT / "munchkindb.sqlite"
OUTPUT = ROOT / "recovery_report.md"


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as stream:
        return sum(1 for line in stream if line.strip())


def mib(paths: list[Path]) -> float:
    return sum(path.stat().st_size for path in paths if path.is_file()) / 1024 / 1024


def main() -> int:
    inventory = json.loads(INVENTORY_STATS.read_text(encoding="utf-8"))
    parsed = json.loads(PARSE_STATS.read_text(encoding="utf-8"))
    validation = json.loads(VALIDATION.read_text(encoding="utf-8")) if VALIDATION.exists() else {}
    resource_stats = json.loads(RESOURCE_STATS.read_text(encoding="utf-8")) if RESOURCE_STATS.exists() else {}
    official_unnatural_axe = (
        json.loads(OFFICIAL_UNNATURAL_AXE.read_text(encoding="utf-8"))
        if OFFICIAL_UNNATURAL_AXE.exists()
        else {}
    )
    site_build = json.loads(SITE_BUILD.read_text(encoding="utf-8")) if SITE_BUILD.exists() else {}
    site_validation = (
        json.loads(SITE_VALIDATION.read_text(encoding="utf-8"))
        if SITE_VALIDATION.exists()
        else {}
    )
    numbered_catalog = json.loads(NUMBERED_SETS.read_text(encoding="utf-8")) if NUMBERED_SETS.exists() else {}
    numbered_sets = list(numbered_catalog.get("sets", []))
    official_list_sets = sum(item.get("coverage") == "official_card_list" for item in numbered_sets)
    metadata_only_sets = sum(item.get("coverage") == "set_metadata_only" for item in numbered_sets)
    ruling_items = json.loads((ROOT / "exports" / "rulings.json").read_text(encoding="utf-8"))
    ruling_redirects = sum(
        len(item.get("internal_refs", [])) == 1
        and not item.get("card_refs")
        and not item.get("external_refs")
        for item in ruling_items
    )
    connection = sqlite3.connect(DB_PATH)
    cards_total, cards_full, cards_partial, cards_ru = connection.execute(
        """SELECT COUNT(*),
                  SUM(recovery_status = 'full_page'),
                  SUM(recovery_status = 'set_listing_only'),
                  SUM(TRIM(title_ru) <> '')
           FROM canonical_card"""
    ).fetchone()
    sets = connection.execute("SELECT COUNT(DISTINCT slug) FROM set_version").fetchone()[0]
    rulings = connection.execute("SELECT COUNT(DISTINCT slug) FROM ruling_version").fetchone()[0]
    pages = connection.execute("SELECT COUNT(DISTINCT page_key) FROM generic_page_version").fetchone()[0]
    faq_pages = connection.execute(
        "SELECT COUNT(DISTINCT page_key) FROM generic_page_version WHERE category = 'faq'"
    ).fetchone()[0]
    comments = connection.execute("SELECT COUNT(DISTINCT comment_id) FROM comment_version").fetchone()[0]
    specials = connection.execute(
        """SELECT COUNT(DISTINCT slug) FROM card_version
        WHERE TRIM(specials_text) <> '' OR TRIM(faq_text) <> '' OR TRIM(facts_text) <> ''
           OR TRIM(errata_ru) <> '' OR TRIM(errata_en) <> '' OR TRIM(changes_19_text) <> ''"""
    ).fetchone()[0]
    set_edges = connection.execute("SELECT COUNT(*) FROM canonical_card_set").fetchone()[0]
    external_refs = connection.execute("SELECT COUNT(*) FROM external_reference").fetchone()[0]
    connection.close()

    cc_manifest = count_lines(ROOT / "data" / "inventory" / "download_manifest.jsonl")
    wb_manifest = count_lines(ROOT / "data" / "inventory" / "wayback_download_manifest.jsonl")
    raw_files = list((ROOT / "raw").rglob("*"))
    raw_size = mib(raw_files)
    full_rate = (cards_full / cards_total * 100) if cards_total else 0
    ru_rate = (cards_ru / cards_total * 100) if cards_total else 0
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    report = f"""# MunchkinDB recovery report

Generated: {generated}

## Current outcome

The archive inventory phase is complete for Wayback Machine and all published
Common Crawl collections. A reproducible raw-download and Drupal parser pipeline
is working, and the current SQLite/JSON/CSV exports contain both full card pages
and partial cards recovered from set listings. A dependency-free static site is
built from those exports for local use and conventional static hosting.

## Archive inventory

- Common Crawl: 127/127 collections queried; {inventory['raw_records_by_source'].get('commoncrawl', 0):,} raw index records; {inventory['unique_urls_by_source'].get('commoncrawl', 0):,} normalized URLs.
- Wayback Machine: 8/8 domain-index pages downloaded; {inventory['raw_records_by_source'].get('wayback', 0):,} raw CDX records; {inventory['unique_urls_by_source'].get('wayback', 0):,} normalized URLs.
- Arquivo.pt: exact URL history and CDX wildcard checks returned zero results.
- Combined: {inventory['raw_index_records']:,} raw records, {inventory['unique_captures_by_url_digest_source']:,} distinct source/URL/digest captures, {inventory['unique_normalized_urls']:,} normalized URLs, and {inventory['unique_normalized_paths']:,} URL paths.
- Candidate paths: {inventory['candidate_card_slugs']:,} card slugs, {inventory['candidate_set_slugs']:,} set slugs, and {inventory['candidate_ruling_slugs']:,} ruling-like slugs. Two ruling-like paths are unrelated payday-loan spam and are excluded from structured data.

## Downloaded raw data

- Common Crawl WARC records/payloads: {cc_manifest:,}.
- Wayback raw-replay payloads: {wb_manifest:,}.
- Current raw directory size: {raw_size:.1f} MiB.
- Every downloaded item has source coordinates, timestamp, digest, payload checksum, and local path in a JSON sidecar/manifest.
- Downloaded non-HTML captures: {resource_stats.get('downloaded_non_html_captures', 0):,} across {resource_stats.get('unique_resource_paths', 0):,} paths.
- Resource classification is heuristic: {resource_stats.get('by_classification', {}).get('drupal_munchkindb_candidate', 0):,} Drupal/MunchkinDB candidates, {resource_stats.get('by_classification', {}).get('unrelated_legacy_candidate', 0):,} unrelated legacy candidates, {resource_stats.get('by_classification', {}).get('post_2024_domain_reuse', 0):,} post-2024 domain-reuse captures, and {resource_stats.get('by_classification', {}).get('unclassified', 0):,} unclassified.

## Structured recovery

- Canonical cards: {cards_total:,}.
- Full card pages: {cards_full:,} ({full_rate:.1f}%).
- Set-listing-only partial cards: {cards_partial:,}.
- Cards with Russian title: {cards_ru:,} ({ru_rate:.1f}%).
- Sets: {sets:,}.
- Card-to-set relationships: {set_edges:,}.
- Ruling records: {rulings:,}; substantive explanations: {rulings - ruling_redirects:,}; original FAQ link aliases retained as redirects: {ruling_redirects:,}.
- Generic content pages: {pages:,}; standalone FAQ pages: {faq_pages:,}.
- Unique archived comments: {comments:,}.
- Cards with application notes, FAQ, facts, or errata: {specials:,}.
- External proof/source links: {external_refs:,}.
- Parser-rejected path/type mismatches: {parsed.get('rejected_path_mismatches', 0):,}; raw evidence remains preserved.

## Validation

- Validation status: {validation.get('status', 'not run')}.
- Payload SHA-256 checksums verified: {validation.get('payload_checksums_verified', 0):,}.
- SQLite integrity check: {validation.get('database', {}).get('integrity_check', 'not run')}.
- Handoff control cards verified as full pages: {validation.get('control_cards_verified', 0):,}.
- Card inventory audit: {validation.get('card_inventory', {}).get('candidate_slugs', 0):,} candidate slugs, {validation.get('card_inventory', {}).get('full_pages', 0):,} full pages, {validation.get('card_inventory', {}).get('set_listing_only_candidates', 0):,} legitimate set-listing-only cards, {validation.get('card_inventory', {}).get('excluded_malformed_slugs', 0):,} malformed archive URLs excluded, and {len(validation.get('card_inventory', {}).get('missing_slugs', [])):,} missing legitimate cards.
- Set inventory audit: {validation.get('set_inventory', {}).get('candidate_slugs', 0):,} indexed slugs, {validation.get('set_inventory', {}).get('exported_slugs', 0):,} recovered sets, and {len(validation.get('set_inventory', {}).get('missing_slugs', [])):,} missing sets.
- Ruling inventory audit: {validation.get('ruling_inventory', {}).get('candidate_slugs', 0):,} indexed paths, {validation.get('ruling_inventory', {}).get('exported_slugs', 0):,} recovered records, {validation.get('ruling_inventory', {}).get('excluded_spam_slugs', 0):,} excluded spam paths, and {len(validation.get('ruling_inventory', {}).get('missing_slugs', [])):,} missing legitimate paths.
- External official-source payloads verified: {validation.get('external_payload_checksums_verified', 0):,}.

## Browsable site

- Generated HTML pages: {site_build.get('html_pages', 0):,}.
- Search-index records: {site_build.get('search_records', 0):,}.
- Visible sets: {site_build.get('sets', 0):,} in {site_build.get('set_groups', 0):,} archived menu groups; {site_build.get('empty_sets_removed', 0):,} empty sets removed and {site_build.get('set_aliases_merged', 0):,} duplicate aliases merged.
- Site validation: {site_validation.get('status', 'not run')}; internal references checked: {site_validation.get('internal_references_checked', 0):,}.
- Proof links embedded at their original text markers: {site_validation.get('inline_proof_links', 0):,}.
- Question-and-answer pairs rendered as structured Q&A cards: {site_validation.get('qa_items', 0):,}.
- FAQ catalog: {site_validation.get('faq_entries', 0):,} materials in {site_validation.get('faq_groups', 0):,} thematic groups, including {site_validation.get('faq_rulings', 0):,} migrated ruling pages.
- The separate rulings section is removed; all {site_validation.get('legacy_ruling_redirects', 0):,} historical detail URLs and its former index redirect into FAQ.
- Duplicate or link-only ruling records redirected to canonical FAQ pages: {site_validation.get('ruling_redirects', 0):,}.
- The site is static and needs no application server or database at runtime.
- Card images are intentionally excluded from the public-ready build pending a separate rights review.
- Current numbered classic-Munchkin catalog entries: {len(numbered_sets):,}; newer supplements with a full official card list: {official_list_sets:,}; metadata-only newer supplements: {metadata_only_sets:,}.

## Official catalog cross-check

- Unnatural Axe official physical cards: {official_unnatural_axe.get('official_physical_cards', 0):,}; unique named cards: {official_unnatural_axe.get('official_unique_named_cards', 0):,}.
- Recovered unique named cards: {official_unnatural_axe.get('recovered_unique_named_cards', 0):,}; normalized name matches: {official_unnatural_axe.get('matched_unique_names', 0):,}.
- Missing official names: {len(official_unnatural_axe.get('missing_from_recovered_by_normalized_name', [])):,}; extra recovered names: {len(official_unnatural_axe.get('extra_in_recovered_by_normalized_name', [])):,}.
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
"""
    OUTPUT.write_text(report, encoding="utf-8")
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
