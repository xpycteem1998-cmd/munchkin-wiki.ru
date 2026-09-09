#!/usr/bin/env python3
"""Parse downloaded Drupal pages into SQLite and JSON exports."""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterator
from urllib.parse import unquote, urljoin, urlsplit


ROOT = Path(__file__).resolve().parents[1]
RAW_ROOTS = (ROOT / "raw" / "commoncrawl", ROOT / "raw" / "wayback")
DB_PATH = ROOT / "munchkindb.sqlite"
EXPORT_DIR = ROOT / "exports"
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    parent: "Node | None" = None
    children: list["Node | str"] = field(default_factory=list)

    @property
    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())


class DOMParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {name: value or "" for name, value in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def descendants(node: Node) -> Iterator[Node]:
    for child in node.children:
        if isinstance(child, Node):
            yield child
            yield from descendants(child)


def text_content(node: Node) -> str:
    values: list[str] = []

    def visit(current: Node) -> None:
        for child in current.children:
            if isinstance(child, str):
                values.append(child)
            elif child.tag == "br":
                values.append("\n")
            else:
                visit(child)

    visit(node)
    return re.sub(r"[ \t\r\f\v]+", " ", "".join(values)).replace(" \n", "\n").strip()


def find_first(node: Node, *, tag: str | None = None, id_value: str | None = None, class_name: str | None = None) -> Node | None:
    for child in descendants(node):
        if tag is not None and child.tag != tag:
            continue
        if id_value is not None and child.attrs.get("id") != id_value:
            continue
        if class_name is not None and class_name not in child.classes:
            continue
        return child
    return None


def find_all(node: Node, *, tag: str | None = None, class_name: str | None = None) -> list[Node]:
    output = []
    for child in descendants(node):
        if tag is not None and child.tag != tag:
            continue
        if class_name is not None and class_name not in child.classes:
            continue
        output.append(child)
    return output


def links(node: Node, base_url: str) -> list[tuple[str, str]]:
    output = []
    for anchor in find_all(node, tag="a"):
        href = anchor.attrs.get("href")
        if href:
            output.append((urljoin(base_url, href), text_content(anchor)))
    return output


def slug_from(url: str, prefix: str) -> str | None:
    path = unquote(urlsplit(url).path).rstrip("/")
    marker = f"/{prefix}/"
    if not path.startswith(marker):
        return None
    return path[len(marker) :]


def parse_title_pair(title: str) -> tuple[str, str]:
    if " / " in title:
        title_en, title_ru = title.split(" / ", 1)
        return title_en.strip(), title_ru.strip()
    return title.strip(), ""


def field_links(root: Node, class_name: str, prefix: str, base_url: str) -> list[dict[str, str]]:
    section = find_first(root, class_name=class_name)
    if section is None:
        return []
    output = []
    for url, title in links(section, base_url):
        slug = slug_from(url, prefix)
        if slug is not None:
            output.append({"slug": slug, "title": title})
    return output


def field_text(root: Node, class_name: str) -> str:
    node = find_first(root, class_name=class_name)
    return text_content(node) if node else ""


def field_item_texts(root: Node, class_name: str) -> list[str]:
    node = find_first(root, class_name=class_name)
    if node is None:
        return []
    values = [text_content(item) for item in find_all(node, class_name="field-item")]
    return list(dict.fromkeys(value for value in values if value))


def parse_card(root: Node, url: str) -> dict[str, object]:
    title_node = find_first(root, id_value="page-title")
    title = text_content(title_node) if title_node else ""
    title_en, title_ru = parse_title_pair(title)
    content_fields = (
        ("specials_text", "field-name-field-specials"),
        ("faq_text", "field-name-field-cardsfaq"),
        ("facts_text", "field-name-field-facts"),
        ("errata_ru", "field-name-field-errata-rus"),
        ("errata_en", "field-name-field-errata-eng"),
        ("changes_19_text", "field-name-field-19changes"),
    )
    content_nodes = [
        (field_name, node)
        for field_name, class_name in content_fields
        if (node := find_first(root, class_name=class_name))
    ]
    ruling_refs = []
    external_refs = []
    inline_external_refs = []
    for target, anchor_text in links(root, url):
        ruling_slug = slug_from(target, "ruling")
        if ruling_slug is not None:
            ruling_refs.append({"slug": ruling_slug, "title": anchor_text, "url": target})
    for field_name, content_node in content_nodes:
        for target, anchor_text in links(content_node, url):
            host = (urlsplit(target).hostname or "").lower()
            if host and host not in ("munchkindb.ru", "www.munchkindb.ru"):
                item = {"url": target, "title": anchor_text}
                external_refs.append(item)
                inline_external_refs.append({**item, "field": field_name})
    return {
        "slug": slug_from(url, "card"),
        "title": title,
        "title_en": title_en,
        "title_ru": title_ru,
        "alternate_names": field_item_texts(root, "field-name-field-alternames"),
        "sets": field_links(root, "field-name-field-cardsset", "set", url),
        "decks": field_links(root, "field-name-field-cardsdeck", "колода", url),
        "tags": field_links(root, "field-name-field-tags", "tag", url),
        "specials_text": field_text(root, "field-name-field-specials"),
        "faq_text": field_text(root, "field-name-field-cardsfaq"),
        "facts_text": field_text(root, "field-name-field-facts"),
        "errata_ru": field_text(root, "field-name-field-errata-rus"),
        "errata_en": field_text(root, "field-name-field-errata-eng"),
        "changes_19_text": field_text(root, "field-name-field-19changes"),
        "ruling_refs": list({item["url"]: item for item in ruling_refs}.values()),
        "external_refs": list({item["url"]: item for item in external_refs}.values()),
        "inline_external_refs": inline_external_refs,
    }


def ancestor_with_class(node: Node, class_name: str) -> Node | None:
    current = node.parent
    while current is not None:
        if class_name in current.classes:
            return current
        current = current.parent
    return None


def parse_set(root: Node, url: str) -> dict[str, object]:
    title_node = find_first(root, id_value="page-title")
    title = text_content(title_node) if title_node else ""
    cards = []
    for row in find_all(root, tag="tr"):
        title_cell = find_first(row, tag="td", class_name="views-field-title")
        local_cell = find_first(row, tag="td", class_name="views-field-field-localname")
        if title_cell is None:
            continue
        card_links = [(target, label) for target, label in links(title_cell, url) if slug_from(target, "card")]
        if not card_links:
            continue
        target, title_en = card_links[0]
        pane = ancestor_with_class(row, "panel-pane")
        pane_title = find_first(pane, tag="h2", class_name="pane-title") if pane else None
        cards.append(
            {
                "slug": slug_from(target, "card"),
                "title_en": title_en,
                "title_ru": text_content(local_cell) if local_cell else "",
                "section": text_content(pane_title) if pane_title else "",
            }
        )
    return {"slug": slug_from(url, "set"), "title": title, "cards": cards}


def parse_ruling(root: Node, url: str) -> dict[str, object]:
    title_node = find_first(root, id_value="page-title")
    title = text_content(title_node) if title_node else ""
    body = find_first(root, class_name="field-name-field-text")
    if body is None:
        body = find_first(root, class_name="field-name-body")
    card_refs = []
    external_refs = []
    inline_external_refs = []
    internal_refs = []
    for target, anchor_text in links(root, url):
        card_slug = slug_from(target, "card")
        if card_slug is not None:
            card_refs.append({"slug": card_slug, "title": anchor_text, "url": target})
    if body is not None:
        for target, anchor_text in links(body, url):
            host = (urlsplit(target).hostname or "").lower()
            if host and host not in ("munchkindb.ru", "www.munchkindb.ru"):
                item = {"url": target, "title": anchor_text}
                external_refs.append(item)
                inline_external_refs.append({**item, "field": "text"})
            elif host in ("munchkindb.ru", "www.munchkindb.ru"):
                internal_refs.append({"url": target, "title": anchor_text})
    body_content = find_first(body, class_name="field-items") if body is not None else None
    return {
        "slug": slug_from(url, "ruling"),
        "title": title,
        "text": text_content(body_content or body) if body else "",
        "card_refs": list({item["url"]: item for item in card_refs}.values()),
        "internal_refs": list({item["url"]: item for item in internal_refs}.values()),
        "external_refs": list({item["url"]: item for item in external_refs}.values()),
        "inline_external_refs": inline_external_refs,
    }


def parse_generic_page(root: Node, url: str) -> dict[str, object]:
    title_node = find_first(root, id_value="page-title")
    body = find_first(root, class_name="field-name-body")
    title = text_content(title_node) if title_node else ""
    body_text = text_content(body) if body else ""
    lower_identity = unquote(f"{url} {title}").lower()
    category = "faq" if "faq" in lower_identity else "page"
    refs = {"card": [], "set": [], "ruling": []}
    external_refs = []
    inline_external_refs = []
    if body is not None:
        for target, anchor_text in links(body, url):
            host = (urlsplit(target).hostname or "").lower()
            if host and host not in ("munchkindb.ru", "www.munchkindb.ru"):
                item = {"url": target, "title": anchor_text}
                external_refs.append(item)
                inline_external_refs.append({**item, "field": "text"})
                continue
            for target_type in refs:
                target_slug = slug_from(target, target_type)
                if target_slug is not None:
                    refs[target_type].append(
                        {"slug": target_slug, "title": anchor_text, "url": target}
                    )
                    break
    return {
        "slug": url,
        "page_key": url,
        "title": title,
        "category": category,
        "text": body_text,
        "card_refs": list({item["url"]: item for item in refs["card"]}.values()),
        "set_refs": list({item["url"]: item for item in refs["set"]}.values()),
        "ruling_refs": list({item["url"]: item for item in refs["ruling"]}.values()),
        "external_refs": list({item["url"]: item for item in external_refs}.values()),
        "inline_external_refs": inline_external_refs,
    }


def parse_comments(root: Node, page_url: str) -> list[dict[str, object]]:
    comments = []
    for article in find_all(root, tag="article", class_name="comment"):
        body = find_first(article, class_name="field-name-comment-body")
        if body is None:
            continue
        title_node = find_first(article, class_name="comment-title")
        title_link = find_first(title_node, tag="a") if title_node else None
        title_href = title_link.attrs.get("href", "") if title_link else ""
        id_match = re.search(r"(?:/comment/|#comment-)(\d+)", title_href)
        if id_match is None:
            id_match = re.search(r"comment-(\d+)", article.attrs.get("id", ""))
        if id_match is None:
            continue
        author_node = find_first(article, class_name="username")
        time_node = find_first(article, tag="time")
        external_refs = []
        for target, anchor_text in links(body, page_url):
            host = (urlsplit(target).hostname or "").lower()
            if host and host not in ("munchkindb.ru", "www.munchkindb.ru"):
                external_refs.append({"url": target, "title": anchor_text})
        comments.append(
            {
                "comment_id": id_match.group(1),
                "title": text_content(title_node) if title_node else "",
                "author": text_content(author_node) if author_node else "",
                "created_at": time_node.attrs.get("datetime", "") if time_node else "",
                "text": text_content(body),
                "url": urljoin(page_url, title_href) if title_href else "",
                "external_refs": list({item["url"]: item for item in external_refs}.values()),
            }
        )
    return comments


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE archived_document (
  id INTEGER PRIMARY KEY,
  normalized_url TEXT NOT NULL,
  original_url TEXT NOT NULL,
  source TEXT NOT NULL,
  collection_name TEXT,
  timestamp TEXT NOT NULL,
  status INTEGER,
  mime TEXT,
  digest TEXT NOT NULL,
  payload_path TEXT NOT NULL,
  page_type TEXT NOT NULL,
  page_title TEXT,
  parsed_json TEXT NOT NULL,
  UNIQUE(source, normalized_url, timestamp, digest)
);
CREATE TABLE card_version (
  document_id INTEGER PRIMARY KEY REFERENCES archived_document(id),
  slug TEXT NOT NULL,
  title_en TEXT,
  title_ru TEXT,
  alternate_names_json TEXT,
  specials_text TEXT,
  faq_text TEXT,
  facts_text TEXT,
  errata_ru TEXT,
  errata_en TEXT,
  changes_19_text TEXT
);
CREATE TABLE card_set (
  document_id INTEGER REFERENCES archived_document(id),
  set_slug TEXT,
  set_title TEXT,
  PRIMARY KEY(document_id, set_slug)
);
CREATE TABLE card_deck (
  document_id INTEGER REFERENCES archived_document(id),
  deck_slug TEXT,
  deck_title TEXT,
  PRIMARY KEY(document_id, deck_slug)
);
CREATE TABLE card_tag (
  document_id INTEGER REFERENCES archived_document(id),
  tag_slug TEXT,
  tag_title TEXT,
  PRIMARY KEY(document_id, tag_slug)
);
CREATE TABLE card_ruling_reference (
  document_id INTEGER REFERENCES archived_document(id),
  ruling_slug TEXT,
  ruling_title TEXT,
  ruling_url TEXT,
  PRIMARY KEY(document_id, ruling_url)
);
CREATE TABLE set_version (
  document_id INTEGER PRIMARY KEY REFERENCES archived_document(id),
  slug TEXT NOT NULL,
  title TEXT
);
CREATE TABLE set_card (
  document_id INTEGER REFERENCES archived_document(id),
  card_slug TEXT,
  title_en TEXT,
  title_ru TEXT,
  section TEXT,
  PRIMARY KEY(document_id, card_slug)
);
CREATE TABLE ruling_version (
  document_id INTEGER PRIMARY KEY REFERENCES archived_document(id),
  slug TEXT NOT NULL,
  title TEXT,
  ruling_text TEXT
);
CREATE TABLE ruling_card_reference (
  document_id INTEGER REFERENCES archived_document(id),
  card_slug TEXT,
  card_title TEXT,
  card_url TEXT,
  PRIMARY KEY(document_id, card_url)
);
CREATE TABLE generic_page_version (
  document_id INTEGER PRIMARY KEY REFERENCES archived_document(id),
  page_key TEXT NOT NULL,
  category TEXT NOT NULL,
  title TEXT,
  body_text TEXT
);
CREATE TABLE generic_page_reference (
  document_id INTEGER REFERENCES archived_document(id),
  target_type TEXT,
  target_slug TEXT,
  target_title TEXT,
  target_url TEXT,
  PRIMARY KEY(document_id, target_url)
);
CREATE TABLE comment_version (
  document_id INTEGER REFERENCES archived_document(id),
  comment_id TEXT,
  title TEXT,
  author TEXT,
  created_at TEXT,
  body_text TEXT,
  comment_url TEXT,
  PRIMARY KEY(document_id, comment_id)
);
CREATE TABLE external_reference (
  document_id INTEGER REFERENCES archived_document(id),
  target_url TEXT,
  anchor_text TEXT,
  PRIMARY KEY(document_id, target_url)
);
CREATE TABLE canonical_card (
  slug TEXT PRIMARY KEY,
  title_en TEXT,
  title_ru TEXT,
  recovery_status TEXT NOT NULL,
  page_document_id INTEGER REFERENCES archived_document(id)
);
CREATE TABLE canonical_card_set (
  card_slug TEXT REFERENCES canonical_card(slug),
  set_slug TEXT,
  set_title TEXT,
  PRIMARY KEY(card_slug, set_slug)
);
"""


def insert_parsed(connection: sqlite3.Connection, metadata: dict[str, object], page_type: str, parsed: dict[str, object]) -> None:
    cursor = connection.execute(
        """INSERT INTO archived_document
        (normalized_url, original_url, source, collection_name, timestamp, status, mime, digest,
         payload_path, page_type, page_title, parsed_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            metadata["normalized_url"], metadata["original_url"], metadata["source"], metadata["collection"],
            metadata["timestamp"], int(str(metadata["status"])), metadata["mime"], metadata["digest"],
            metadata["payload_path"], page_type, parsed.get("title", ""), json.dumps(parsed, ensure_ascii=False),
        ),
    )
    document_id = cursor.lastrowid
    assert document_id is not None
    if page_type == "card":
        connection.execute(
            "INSERT INTO card_version VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                document_id,
                parsed["slug"],
                parsed["title_en"],
                parsed["title_ru"],
                json.dumps(parsed["alternate_names"], ensure_ascii=False),
                parsed["specials_text"],
                parsed["faq_text"],
                parsed["facts_text"],
                parsed["errata_ru"],
                parsed["errata_en"],
                parsed["changes_19_text"],
            ),
        )
        for item in parsed["sets"]:
            connection.execute("INSERT INTO card_set VALUES (?, ?, ?)", (document_id, item["slug"], item["title"]))
        for item in parsed["decks"]:
            connection.execute("INSERT INTO card_deck VALUES (?, ?, ?)", (document_id, item["slug"], item["title"]))
        for item in parsed["tags"]:
            connection.execute("INSERT INTO card_tag VALUES (?, ?, ?)", (document_id, item["slug"], item["title"]))
        for item in parsed["ruling_refs"]:
            connection.execute(
                "INSERT INTO card_ruling_reference VALUES (?, ?, ?, ?)",
                (document_id, item["slug"], item["title"], item["url"]),
            )
    elif page_type == "set":
        connection.execute("INSERT INTO set_version VALUES (?, ?, ?)", (document_id, parsed["slug"], parsed["title"]))
        for item in parsed["cards"]:
            connection.execute(
                "INSERT INTO set_card VALUES (?, ?, ?, ?, ?)",
                (document_id, item["slug"], item["title_en"], item["title_ru"], item["section"]),
            )
    elif page_type == "ruling":
        connection.execute(
            "INSERT INTO ruling_version VALUES (?, ?, ?, ?)",
            (document_id, parsed["slug"], parsed["title"], parsed["text"]),
        )
        for item in parsed["card_refs"]:
            connection.execute(
                "INSERT INTO ruling_card_reference VALUES (?, ?, ?, ?)",
                (document_id, item["slug"], item["title"], item["url"]),
            )
    elif page_type == "page":
        connection.execute(
            "INSERT INTO generic_page_version VALUES (?, ?, ?, ?, ?)",
            (
                document_id,
                parsed["page_key"],
                parsed["category"],
                parsed["title"],
                parsed["text"],
            ),
        )
        for target_type in ("card", "set", "ruling"):
            for item in parsed[f"{target_type}_refs"]:
                connection.execute(
                    "INSERT OR IGNORE INTO generic_page_reference VALUES (?, ?, ?, ?, ?)",
                    (document_id, target_type, item["slug"], item["title"], item["url"]),
                )
    for comment in parsed.get("comments", []):
        connection.execute(
            "INSERT OR IGNORE INTO comment_version VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                document_id,
                comment["comment_id"],
                comment["title"],
                comment["author"],
                comment["created_at"],
                comment["text"],
                comment["url"],
            ),
        )
        for item in comment["external_refs"]:
            connection.execute(
                "INSERT OR IGNORE INTO external_reference VALUES (?, ?, ?)",
                (document_id, item["url"], item["title"]),
            )
    for item in parsed.get("external_refs", []):
        connection.execute(
            "INSERT OR IGNORE INTO external_reference VALUES (?, ?, ?)",
            (document_id, item["url"], item["title"]),
        )


def export_latest(connection: sqlite3.Connection, table: str, slug_column: str, filename: str) -> int:
    query = f"""
    WITH ranked AS (
      SELECT v.{slug_column} AS slug, d.parsed_json,
             ROW_NUMBER() OVER (
               PARTITION BY v.{slug_column}
               ORDER BY d.timestamp DESC,
                        CASE d.source WHEN 'commoncrawl' THEN 0 ELSE 1 END,
                        d.id DESC
             ) AS version_rank
      FROM archived_document d
      JOIN {table} v ON v.document_id = d.id
    )
    SELECT parsed_json FROM ranked WHERE version_rank = 1 ORDER BY slug
    """
    items = [json.loads(row[0]) for row in connection.execute(query)]
    (EXPORT_DIR / filename).write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(items)


def export_comments(connection: sqlite3.Connection) -> int:
    query = """
    WITH ranked AS (
      SELECT cv.*, d.normalized_url AS source_page, d.timestamp AS capture_timestamp,
             ROW_NUMBER() OVER (
               PARTITION BY cv.comment_id
               ORDER BY d.timestamp DESC,
                        CASE d.source WHEN 'commoncrawl' THEN 0 ELSE 1 END,
                        d.id DESC
             ) AS version_rank
      FROM comment_version cv
      JOIN archived_document d ON d.id = cv.document_id
    )
    SELECT comment_id, title, author, created_at, body_text, comment_url,
           source_page, capture_timestamp
    FROM ranked WHERE version_rank = 1 ORDER BY CAST(comment_id AS INTEGER), comment_id
    """
    items = [
        {
            "comment_id": row[0],
            "title": row[1],
            "author": row[2],
            "created_at": row[3],
            "text": row[4],
            "url": row[5],
            "source_page": row[6],
            "capture_timestamp": row[7],
        }
        for row in connection.execute(query)
    ]
    (EXPORT_DIR / "comments.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return len(items)


def build_canonical_cards(connection: sqlite3.Connection) -> dict[str, int]:
    cards: dict[str, dict[str, object]] = {}
    names_from_sets: dict[str, tuple[str, str, str]] = {}
    sets_by_card: dict[str, dict[str, str]] = {}
    for card_slug, title_en, title_ru, set_slug, set_title, timestamp in connection.execute(
        """SELECT sc.card_slug, sc.title_en, sc.title_ru, sv.slug, sv.title, d.timestamp
        FROM set_card sc
        JOIN set_version sv ON sv.document_id = sc.document_id
        JOIN archived_document d ON d.id = sc.document_id
        ORDER BY d.timestamp"""
    ):
        previous = names_from_sets.get(card_slug)
        if previous is None or timestamp >= previous[2]:
            names_from_sets[card_slug] = (title_en or "", title_ru or "", timestamp)
        sets_by_card.setdefault(card_slug, {})[set_slug] = set_title or set_slug

    latest_pages: dict[str, tuple[int, str, dict[str, object]]] = {}
    for document_id, slug, timestamp, parsed_json in connection.execute(
        """SELECT d.id, cv.slug, d.timestamp, d.parsed_json
        FROM card_version cv JOIN archived_document d ON d.id = cv.document_id
        ORDER BY d.timestamp"""
    ):
        latest_pages[slug] = (document_id, timestamp, json.loads(parsed_json))

    all_slugs = sorted(set(names_from_sets) | set(latest_pages))
    output = []
    for slug in all_slugs:
        set_names = names_from_sets.get(slug, ("", "", ""))
        page_record = latest_pages.get(slug)
        page = page_record[2] if page_record else None
        title_en = str(page.get("title_en", "")) if page else ""
        title_ru = str(page.get("title_ru", "")) if page else ""
        title_en = title_en or set_names[0]
        title_ru = title_ru or set_names[1]
        recovery_status = "full_page" if page else "set_listing_only"
        document_id = page_record[0] if page_record else None
        connection.execute(
            "INSERT INTO canonical_card VALUES (?, ?, ?, ?, ?)",
            (slug, title_en, title_ru, recovery_status, document_id),
        )
        related_sets = []
        set_map = sets_by_card.get(slug, {}).copy()
        if page:
            for item in page.get("sets", []):
                set_map[str(item["slug"])] = str(item["title"])
        for set_slug, set_title in sorted(set_map.items()):
            connection.execute(
                "INSERT OR IGNORE INTO canonical_card_set VALUES (?, ?, ?)",
                (slug, set_slug, set_title),
            )
            related_sets.append({"slug": set_slug, "title": set_title})
        output.append(
            {
                "slug": slug,
                "title_en": title_en,
                "title_ru": title_ru,
                "recovery_status": recovery_status,
                "sets": related_sets,
                "page": page,
            }
        )
    (EXPORT_DIR / "cards.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "cards_total": len(output),
        "cards_full_page": sum(item["recovery_status"] == "full_page" for item in output),
        "cards_set_listing_only": sum(item["recovery_status"] == "set_listing_only" for item in output),
        "cards_with_russian_title": sum(bool(item["title_ru"].strip()) for item in output),
    }


def export_csv_files(connection: sqlite3.Connection) -> None:
    with (EXPORT_DIR / "cards.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("slug", "title_en", "title_ru", "recovery_status", "set_slugs"))
        for row in connection.execute(
            """SELECT c.slug, c.title_en, c.title_ru, c.recovery_status,
                      COALESCE(GROUP_CONCAT(cs.set_slug, '|'), '')
               FROM canonical_card c
               LEFT JOIN canonical_card_set cs ON cs.card_slug = c.slug
               GROUP BY c.slug ORDER BY c.slug"""
        ):
            writer.writerow(row)
    with (EXPORT_DIR / "sets.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("slug", "title", "listed_card_count"))
        for row in connection.execute(
            """SELECT sv.slug, MAX(sv.title), COUNT(DISTINCT sc.card_slug)
               FROM set_version sv
               LEFT JOIN set_card sc ON sc.document_id = sv.document_id
               GROUP BY sv.slug ORDER BY sv.slug"""
        ):
            writer.writerow(row)
    with (EXPORT_DIR / "rulings.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("slug", "title", "text", "related_card_slugs"))
        query = """
        WITH ranked AS (
          SELECT rv.document_id, rv.slug,
                 ROW_NUMBER() OVER (
                   PARTITION BY rv.slug
                   ORDER BY d.timestamp DESC,
                            CASE d.source WHEN 'commoncrawl' THEN 0 ELSE 1 END,
                            d.id DESC
                 ) AS version_rank
          FROM ruling_version rv JOIN archived_document d ON d.id = rv.document_id
        )
        SELECT rv.slug, rv.title, rv.ruling_text,
               COALESCE(GROUP_CONCAT(DISTINCT rcr.card_slug), '')
        FROM ruling_version rv
        JOIN ranked ON ranked.document_id = rv.document_id AND ranked.version_rank = 1
        LEFT JOIN ruling_card_reference rcr ON rcr.document_id = rv.document_id
        GROUP BY rv.document_id ORDER BY rv.slug
        """
        for row in connection.execute(query):
            writer.writerow(row)
    with (EXPORT_DIR / "pages.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("page_key", "category", "title", "text"))
        query = """
        WITH ranked AS (
          SELECT gp.*, ROW_NUMBER() OVER (
            PARTITION BY gp.page_key ORDER BY d.timestamp DESC,
              CASE d.source WHEN 'commoncrawl' THEN 0 ELSE 1 END, d.id DESC
          ) AS version_rank
          FROM generic_page_version gp JOIN archived_document d ON d.id = gp.document_id
        )
        SELECT page_key, category, title, body_text
        FROM ranked WHERE version_rank = 1 ORDER BY page_key
        """
        writer.writerows(connection.execute(query))
    with (EXPORT_DIR / "comments.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("comment_id", "title", "author", "created_at", "text", "comment_url", "source_page"))
        query = """
        WITH ranked AS (
          SELECT cv.*, d.normalized_url AS source_page,
                 ROW_NUMBER() OVER (
                   PARTITION BY cv.comment_id ORDER BY d.timestamp DESC,
                     CASE d.source WHEN 'commoncrawl' THEN 0 ELSE 1 END, d.id DESC
                 ) AS version_rank
          FROM comment_version cv JOIN archived_document d ON d.id = cv.document_id
        )
        SELECT comment_id, title, author, created_at, body_text, comment_url, source_page
        FROM ranked WHERE version_rank = 1 ORDER BY CAST(comment_id AS INTEGER), comment_id
        """
        writer.writerows(connection.execute(query))


def main() -> int:
    EXPORT_DIR.mkdir(exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    connection = sqlite3.connect(DB_PATH)
    connection.executescript(SCHEMA)
    parsed_counts = {"card": 0, "set": 0, "ruling": 0, "page": 0}
    skipped = 0
    rejected = []
    metadata_paths = [path for raw_root in RAW_ROOTS for path in raw_root.glob("*/*.json")]
    for metadata_path in sorted(metadata_paths):
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        payload_path = ROOT / str(metadata["payload_path"])
        if payload_path.suffix.lower() != ".html":
            skipped += 1
            continue
        url = str(metadata["normalized_url"])
        path = urlsplit(url).path
        if path.startswith("/card/"):
            page_type = "card"
            parser_function = parse_card
        elif path.startswith("/set/"):
            page_type = "set"
            parser_function = parse_set
        elif path.startswith("/ruling/"):
            page_type = "ruling"
            parser_function = parse_ruling
        else:
            page_type = "page"
            parser_function = parse_generic_page
        parser = DOMParser()
        parser.feed(payload_path.read_text(encoding="utf-8", errors="replace"))
        body = find_first(parser.root, tag="body")
        body_classes = body.classes if body else set()
        valid = {
            "card": "page-type-card" in body_classes or find_first(parser.root, class_name="field-name-field-cardsset") is not None,
            "set": "section-set" in body_classes,
            "ruling": "page-type-ruling" in body_classes,
            "page": find_first(parser.root, class_name="field-name-body") is not None,
        }[page_type]
        if not valid:
            if page_type == "page":
                skipped += 1
                continue
            rejected.append(
                {
                    "normalized_url": url,
                    "timestamp": metadata["timestamp"],
                    "digest": metadata["digest"],
                    "expected_page_type": page_type,
                    "body_classes": sorted(body_classes),
                    "payload_path": metadata["payload_path"],
                }
            )
            continue
        parsed = parser_function(parser.root, url)
        parsed["comments"] = parse_comments(parser.root, url)
        insert_parsed(connection, metadata, page_type, parsed)
        parsed_counts[page_type] += 1
    connection.commit()
    latest_counts = {
        "card_pages": export_latest(connection, "card_version", "slug", "card_pages.json"),
        "sets": export_latest(connection, "set_version", "slug", "sets.json"),
        "rulings": export_latest(connection, "ruling_version", "slug", "rulings.json"),
        "pages": export_latest(connection, "generic_page_version", "page_key", "pages.json"),
        "comments": export_comments(connection),
    }
    canonical_counts = build_canonical_cards(connection)
    connection.commit()
    export_csv_files(connection)
    stats = {
        "parsed_versions": parsed_counts,
        "latest_exports": latest_counts,
        "canonical_cards": canonical_counts,
        "skipped_payloads": skipped,
        "rejected_path_mismatches": len(rejected),
    }
    (EXPORT_DIR / "rejected_pages.json").write_text(
        json.dumps(rejected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (EXPORT_DIR / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
