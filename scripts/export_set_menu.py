#!/usr/bin/env python3
"""Recover the original hierarchical set navigation from archived Drupal HTML."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

from parse_archive import DOMParser, Node, find_first, text_content


ROOT = Path(__file__).resolve().parents[1]
RAW_ROOTS = (ROOT / "raw" / "commoncrawl", ROOT / "raw" / "wayback")
OUTPUT = ROOT / "exports" / "set_menu.json"

# Duplicate historical set URLs found in the archive. Each alias contains the
# same cards as its menu target, except adventure-time, which is a strict subset.
ALIASES = {
    "adventure-time": "munchkin-adventure-time",
    "apocalypse": "munchkin-apocalypse",
    "axe-cop": "munchkin-axe-cop",
    "demented-dungeon": "demented-dungeons",
    "fairy-dust": "munchkin-fairy-dust",
    "munchkin-naughty-nice": "naughty-nice",
    "munchkin-penny-arcade": "penny-arcade",
    "munchkin-skullkickers": "skullkickers",
}


def direct_children(node: Node, tag: str) -> list[Node]:
    return [child for child in node.children if isinstance(child, Node) and child.tag == tag]


def parse_list(node: Node) -> list[dict[str, object]]:
    items = []
    for list_item in direct_children(node, "li"):
        anchors = direct_children(list_item, "a")
        if not anchors:
            continue
        anchor = anchors[0]
        path = unquote(urlsplit(anchor.attrs.get("href", "")).path).rstrip("/")
        if not path.startswith("/set/"):
            continue
        item: dict[str, object] = {
            "slug": path.removeprefix("/set/"),
            "label": text_content(anchor),
            "title": anchor.attrs.get("title", ""),
        }
        nested = direct_children(list_item, "ul")
        if nested:
            item["children"] = parse_list(nested[0])
        items.append(item)
    return items


def entry_count(items: list[dict[str, object]]) -> int:
    return sum(1 + entry_count(list(item.get("children", []))) for item in items)


def main() -> int:
    candidates = []
    for raw_root in RAW_ROOTS:
        for metadata_path in raw_root.glob("*/*.json"):
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            payload_path = ROOT / str(metadata["payload_path"])
            if payload_path.suffix.lower() != ".html":
                continue
            parser = DOMParser()
            parser.feed(payload_path.read_text(encoding="utf-8", errors="replace"))
            navigation = find_first(parser.root, id_value="block-menu-menu-sets-menu")
            if navigation is None:
                continue
            content = find_first(navigation, class_name="block-content")
            lists = direct_children(content, "ul") if content else []
            if not lists:
                continue
            groups = parse_list(lists[0])
            candidates.append((entry_count(groups), str(metadata["timestamp"]), metadata, groups))
    if not candidates:
        raise RuntimeError("no archived set menu found")

    count, timestamp, metadata, groups = max(candidates, key=lambda item: (item[0], item[1]))
    output = {
        "source": {
            "normalized_url": metadata["normalized_url"],
            "timestamp": timestamp,
            "payload_path": metadata["payload_path"],
            "digest": metadata["digest"],
        },
        "menu_entries": count,
        "aliases": ALIASES,
        "groups": groups,
    }
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "menu_entries": count, "groups": len(groups)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
