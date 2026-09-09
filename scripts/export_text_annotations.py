#!/usr/bin/env python3
"""Recover inline semantics from the exact archived versions used by the exports.

Reads raw/ and SQLite read-only. Produces a portable annotation export; plain
archive exports are not changed. No network access is required.
"""
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from parse_archive import DOMParser, Node, find_first, text_content

ROOT = Path(__file__).resolve().parents[1]
FIELDS = {
    "specials_text": "field-name-field-specials", "faq_text": "field-name-field-cardsfaq",
    "facts_text": "field-name-field-facts", "errata_ru": "field-name-field-errata-rus",
    "errata_en": "field-name-field-errata-eng", "changes_19_text": "field-name-field-19changes",
}


def resolve_url(value, base):
    value = value.strip()
    if re.match(r"^(?:www\.|forums\.sjgames\.com/|munchkin\.game/)", value, re.I):
        value = "https://" + value
    target = urljoin(base, value)
    parsed = urlsplit(target)
    if parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password:
        return target
    return ""


def annotate(node, base):
    chars, labels, tags = [], [], []

    def visit(current, active):
        for child in current.children:
            if isinstance(child, str):
                chars.extend(child)
                labels.extend([active] * len(child))
                continue
            if child.tag == "br":
                chars.append("\n")
                labels.append(active)
                continue
            tag = {"s": "del", "strike": "del", "b": "strong", "i": "em"}.get(child.tag, child.tag)
            spec = {"tag": tag}
            if tag == "a":
                spec["url"] = resolve_url(child.attrs.get("href", ""), base)
            if tag in ("del", "strong", "em") or (tag == "a" and spec["url"]):
                ident = len(tags)
                tags.append(spec)
                visit(child, active + (ident,))
            else:
                visit(child, active)

    visit(node, ())
    raw = "".join(chars)
    out, marks = [], []
    for match in re.finditer(r"[ \t\r\f\v]+|[^ \t\r\f\v]", raw):
        value = match.group()
        out.append(" " if value[0] in " \t\r\f\v" else value)
        marks.append(labels[match.start()])
    keep = [i for i, char in enumerate(out) if not (char == " " and i + 1 < len(out) and out[i + 1] == "\n")]
    while keep and out[keep[0]].isspace(): keep.pop(0)
    while keep and out[keep[-1]].isspace(): keep.pop()
    result = "".join(out[i] for i in keep)
    positions = {}
    for offset, index in enumerate(keep):
        for ident in marks[index]:
            positions.setdefault(ident, []).append(offset)
    spans = [{**tags[ident], "start": pos[0], "end": pos[-1] + 1} for ident, pos in positions.items()]
    assert result == text_content(node), "annotation offsets differ from archive text"
    return result, spans


def main():
    db = sqlite3.connect(f"file:{ROOT / 'munchkindb.sqlite'}?mode=ro", uri=True)
    versions = {}
    for url, stamp, provider, payload, parsed_json in db.execute(
        "SELECT normalized_url,timestamp,source,payload_path,parsed_json FROM archived_document ORDER BY timestamp DESC,id DESC"
    ):
        parsed = json.loads(parsed_json)
        identity = str(parsed.get("page_key") or parsed.get("slug", ""))
        versions.setdefault(identity, []).append((url, stamp, provider, payload, parsed))
    result = {}
    for kind, filename in (("card", "card_pages.json"), ("ruling", "rulings.json"), ("page", "pages.json")):
        for item in json.loads((ROOT / "exports" / filename).read_text()):
            identity = str(item.get("page_key") or item["slug"])
            fields = FIELDS if kind == "card" else {"text": "field-name-body"}
            chosen = next((row for row in versions.get(identity, []) if all(row[4].get(key, "") == item.get(key, "") for key in fields)), None)
            if not chosen:
                raise RuntimeError(f"No exact source version for {kind}/{identity}")
            url, stamp, provider, payload, _ = chosen
            data = (ROOT / payload).read_bytes()
            parser = DOMParser()
            parser.feed(data.decode("utf-8", errors="replace"))
            annotated = {}
            for field, css in fields.items():
                if not item.get(field): continue
                node = find_first(parser.root, class_name=css)
                if kind == "ruling":
                    node = find_first(parser.root, class_name="field-name-field-text") or node
                    node = (find_first(node, class_name="field-items") or node) if node else None
                if node is None: raise RuntimeError(f"Missing field {identity}/{field}")
                plain, spans = annotate(node, url)
                if plain != item[field]: raise RuntimeError(f"Text changed: {identity}/{field}")
                annotated[field] = {"sha256": hashlib.sha256(plain.encode()).hexdigest(), "spans": spans}
            result[f"{kind}/{identity}"] = {"url": url, "capture": stamp, "provider": provider,
                "payload_sha256": hashlib.sha256(data).hexdigest(), "fields": annotated}
    (ROOT / "exports" / "text_annotations.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"Annotated {len(result)} exact archived versions")


if __name__ == "__main__": main()
