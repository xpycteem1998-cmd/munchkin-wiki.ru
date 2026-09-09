#!/usr/bin/env python3
"""Build a dependency-free static rules reference from recovered exports."""

from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import re
import shutil
import unicodedata
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from site_content import (SITE_URL, EDITORIAL, annotated_inline, attach_annotations,
                          source_note, feedback, number, counted, card_has_notes, card_names, ROUTE_MAP, link_health)


ROOT = Path(__file__).resolve().parents[1]
EXPORTS = ROOT / "exports"
SOURCE = ROOT / "site_src"
DEFAULT_OUTPUT = ROOT / "site"
FAQ_GROUPS_FILE = SOURCE / "faq_groups.json"


def source_version(filename: str) -> str:
    return hashlib.sha256((SOURCE / filename).read_bytes()).hexdigest()[:12]


STYLE_VERSION = source_version("styles.css")
SCRIPT_VERSION = source_version("app.js")


def esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def route(kind: str, slug: str = "") -> str:
    base = f"/{kind}/" if kind else "/"
    return base + (quote(slug, safe="-._~") + "/" if slug else "")


def write_page(output: Path, kind: str, slug: str, content: str) -> None:
    directory = output / kind / slug if kind and slug else output / kind if kind else output
    directory.mkdir(parents=True, exist_ok=True)
    target = route(kind, slug)
    canonical = re.search(r'<link rel="canonical" href="([^"]+)">', content)
    if canonical:
        content = content.replace(canonical.group(0), f'<link rel="canonical" href="{SITE_URL}{canonical.group(1)}">')
    else:
        content = content.replace('</head>', f'<link rel="canonical" href="{SITE_URL}{target}"><meta property="og:url" content="{SITE_URL}{target}"></head>')
    if kind == "search":
        content = content.replace('</head>', '<meta name="robots" content="noindex,follow"></head>')
    if (kind == "cards" and slug) or (kind == "faq" and slug):
        content = content.replace('</main>', feedback(target) + '</main>')
    (directory / "index.html").write_text(content, encoding="utf-8")


def page_slug(page_key: str) -> str:
    path = unquote(urlsplit(page_key).path).strip("/") or "home"
    value = path.replace("/", "--")
    if value in (".", ".."):
        value = "page-" + value.replace(".", "dot")
    return value


def faq_ruling_slug(slug: str) -> str:
    return f"ruling--{slug}"


def faq_ruling_route(slug: str) -> str:
    return route("faq", faq_ruling_slug(slug))


def normalized_page_path(url: str) -> str:
    return unquote(urlsplit(url).path).rstrip("/").casefold()


def build_ruling_redirects(
    rulings: list[dict[str, object]], pages: list[dict[str, object]]
) -> dict[str, dict[str, str]]:
    page_routes = {
        normalized_page_path(str(item["page_key"])): route(
            "faq" if item.get("category") == "faq" else "pages",
            page_slug(str(item["page_key"])),
        )
        for item in pages
    }
    faq_pages_by_text = {
        re.sub(r"\s+", " ", str(item.get("text", ""))).strip().casefold(): {
            "target": route("faq", page_slug(str(item["page_key"]))),
            "title": str(item.get("title") or "FAQ"),
        }
        for item in pages
        if item.get("category") == "faq" and str(item.get("text", "")).strip()
    }
    redirects: dict[str, dict[str, str]] = {}
    for ruling in rulings:
        body_text = re.sub(r"^Текст:\s*", "", str(ruling.get("text", "")), flags=re.I)
        body_text = re.sub(r"\s+", " ", body_text).strip().casefold()
        duplicate = faq_pages_by_text.get(body_text)
        if duplicate:
            redirects[str(ruling["slug"])] = {
                "target": duplicate["target"],
                "title": str(ruling.get("title") or duplicate["title"]),
                "label": duplicate["title"],
            }
            continue
        refs = list(ruling.get("internal_refs", []))
        if len(refs) != 1 or ruling.get("card_refs") or ruling.get("external_refs"):
            continue
        ref = refs[0]
        target = page_routes.get(normalized_page_path(str(ref.get("url", ""))))
        link_text = re.sub(r"\s+", " ", str(ref.get("title", ""))).strip().casefold()
        if target and body_text == link_text:
            redirects[str(ruling["slug"])] = {
                "target": target,
                "title": str(ruling.get("title") or ref.get("title") or ruling["slug"]),
                "label": str(ref.get("title") or ruling.get("title") or "Открыть FAQ"),
            }
    return redirects


PROOF_MARKER = re.compile(r"\[\s*пруф(?:ы)?\b[^\]\r\n]*\]", re.IGNORECASE)


def is_proof_ref(item: dict[str, object]) -> bool:
    return "пруф" in str(item.get("title", "")).casefold() and urlsplit(str(item.get("url", ""))).scheme in ("http", "https")


class ProofLinker:
    """Match preserved external anchors to proof markers in their source field."""

    def __init__(
        self,
        inline_refs: object,
        external_refs: list[dict[str, object]],
        page: dict[str, object] | None = None,
    ) -> None:
        self.has_inline_data = isinstance(inline_refs, list)
        self.by_field: dict[str, list[dict[str, object]]] = defaultdict(list)
        if self.has_inline_data:
            for item in inline_refs:
                if isinstance(item, dict) and is_proof_ref(item):
                    self.by_field[str(item.get("field", ""))].append(item)
        self.fallback = [item for item in external_refs if is_proof_ref(item)]
        self.field_offsets: dict[str, int] = defaultdict(int)
        self.fallback_offset = 0
        self.used_urls: set[str] = set()
        self.page = page or {}
        self.annotations = self.page.get("_annotation", {}).get("fields", {})
        self.text_offsets = defaultdict(int)

    def render_inline(self, value: str, field: str) -> str:
        if field in self.annotations:
            original = str(self.page.get(field, ""))
            start = original.find(value, self.text_offsets[field])
            if start < 0:
                raise ValueError(f"Unable to locate rendered text in annotated field {field}: {value[:60]}")
            self.text_offsets[field] = start + len(value)
            return annotated_inline(value, start, self.annotations[field]["spans"], self.used_urls, not str(self.page.get("page_key", "")).rstrip("/").endswith("/welcome"))
        refs = list(self.by_field.get(field, [])) if self.has_inline_data else list(self.fallback)
        offset = self.field_offsets[field] if self.has_inline_data else self.fallback_offset
        consumed = 0

        def replace_marker(match: re.Match[str]) -> str:
            nonlocal consumed
            position = offset + consumed
            if position >= len(refs):
                return '<span class="missing-proof">Источник не восстановлен</span>'
            item = refs[position]
            consumed += 1
            target = str(item["url"])
            self.used_urls.add(target)
            return (
                f'<a class="inline-proof" href="{esc(target)}" '
                f'rel="noopener noreferrer" target="_blank">{esc(match.group(0))}</a>'
            )

        rendered = PROOF_MARKER.sub(replace_marker, esc(value))
        if self.has_inline_data:
            self.field_offsets[field] += consumed
        else:
            self.fallback_offset += consumed
        return rendered

    def render(self, value: str, field: str) -> str:
        value = re.sub(r"\r\n?", "\n", value or "").strip()
        if not value:
            return ""
        blocks = re.split(r"\n\s*\n", value)
        rendered = []
        for block in blocks:
            if not block.strip():
                continue
            safe = self.render_inline(block, field).replace(chr(10), "<br>")
            rendered.append(f"<p>{safe}</p>")
        return "".join(rendered)


QUESTION_END = re.compile(r"\?(?:\s*" + PROOF_MARKER.pattern + r")?[\s\"”»)]*$", re.IGNORECASE)


def qa_pairs(value: str) -> tuple[str, list[tuple[str, str]]]:
    lines = re.sub(r"\r\n?", "\n", value or "").split("\n")
    candidates = [
        index
        for index, line in enumerate(lines)
        if QUESTION_END.search(line.strip()) or re.match(r"^\s*В:\s*", line, re.IGNORECASE)
    ]
    question_indexes: list[int] = []
    for index in candidates:
        if not question_indexes or any(
            line.strip() for line in lines[question_indexes[-1] + 1:index]
        ):
            question_indexes.append(index)
    pairs: list[tuple[str, str]] = []
    first_question = len(lines)
    for position, question_index in enumerate(question_indexes):
        next_question = question_indexes[position + 1] if position + 1 < len(question_indexes) else len(lines)
        answer_lines = lines[question_index + 1:next_question]
        while answer_lines and not answer_lines[0].strip():
            answer_lines.pop(0)
        while answer_lines and not answer_lines[-1].strip():
            answer_lines.pop()
        if not answer_lines:
            continue
        question = re.sub(r"^\s*В:\s*", "", lines[question_index], flags=re.IGNORECASE).strip()
        answer_lines[0] = re.sub(r"^\s*О:\s*", "", answer_lines[0], flags=re.IGNORECASE)
        answer = "\n".join(answer_lines).strip()
        if question and answer:
            first_question = min(first_question, question_index)
            pairs.append((question, answer))
    intro = "\n".join(lines[:first_question]).strip() if pairs else value.strip()
    return intro, pairs


def render_qa(value: str, proofs: ProofLinker, field: str) -> str:
    intro, pairs = qa_pairs(value)
    override = EDITORIAL["qa_overrides"].get(str(proofs.page.get("slug", "")))
    if override and not pairs:
        lines = value.splitlines()
        cut = override["split_after_line"]
        intro, pairs = "", [("\n".join(lines[:cut]), "\n".join(lines[cut:]))]
    if not pairs:
        return proofs.render(value, field)
    intro_html = f'<div class="qa-intro">{proofs.render(intro, field)}</div>' if intro else ""
    items = []
    for question, answer in pairs:
        anchor = "qa-" + hashlib.sha256((field + question).encode()).hexdigest()[:12]
        question_html = proofs.render_inline(question, field)
        answer_html = proofs.render(answer, field)
        items.append(
            f'<article class="qa-item" id="{anchor}">'
            '<h3 class="qa-question"><span class="qa-marker" aria-hidden="true">В</span>'
            f'<span>{question_html} <a class="answer-link" href="#{anchor}" aria-label="Ссылка на этот ответ">#</a></span></h3>'
            '<div class="qa-answer"><span class="qa-marker" aria-hidden="true">О</span>'
            f'<div>{answer_html}</div></div></article>'
        )
    return f'<div class="qa-list">{intro_html}{"".join(items)}</div>'


def text_blocks(value: str) -> str:
    value = re.sub(r"\r\n?", "\n", value or "").strip()
    if not value:
        return ""
    blocks = re.split(r"\n\s*\n", value)
    return "".join(
        f'<p>{esc(block).replace(chr(10), "<br>")}</p>' for block in blocks if block.strip()
    )


def shell(
    title: str,
    body: str,
    *,
    active: str = "",
    description: str = "",
    redirect_to: str = "",
) -> str:
    nav = (
        ("cards", "Карточки", "/cards/"),
        ("sets", "Наборы", "/sets/"),
        ("faq", "FAQ", "/faq/"),
        ("contacts", "Контакты", "/contacts/"),
    )
    nav_html = "".join(
        f'<a href="{href}"{(" aria-current=\"page\"" if key == active else "")}>{label}</a>'
        for key, label, href in nav
    )
    full_title = f"{title} — Архив правил Munchkin" if title else "Архив правил Munchkin"
    description = description or f"{title or 'Munchkin Wiki'}: правила, ответы и источники в справочнике Munchkin."
    redirect_head = (
        f'  <meta http-equiv="refresh" content="0; url={esc(redirect_to)}">\n'
        f'  <link rel="canonical" href="{esc(redirect_to)}">\n'
        if redirect_to else ""
    )
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{esc(description)}">
  <meta property="og:title" content="{esc(full_title)}">
  <meta property="og:description" content="{esc(description)}">
  <meta property="og:type" content="website">
  <meta property="og:locale" content="ru_RU">
  <link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">
{redirect_head}  <title>{esc(full_title)}</title>
  <link rel="stylesheet" href="/assets/styles.css?v={STYLE_VERSION}">
</head>
<body>
  <a class="skip-link" href="#main">К содержанию</a>
  <header class="site-header">
    <a class="brand" href="/" aria-label="munchkin-wiki.ru — главная">
      <span class="brand-mark" aria-hidden="true">M</span>
      <span class="brand-wordmark"><span class="brand-name">Munchkin<span class="brand-domain">-wiki.ru</span></span><span class="brand-caption">Правила · Карты · FAQ</span></span>
    </a>
    <nav aria-label="Основная навигация">{nav_html}</nav>
    <a class="search-link" href="/search/">Поиск <kbd>/</kbd></a>
  </header>
  <main id="main">{body}</main>
  <footer class="site-footer">
    <p>Неофициальный восстановленный справочник. Названия Munchkin и MunchkinDB принадлежат их правообладателям.</p>
    <p>Основной источник — сохранённые страницы бывшего munchkindb.ru; актуальные дополнения явно отмечены и снабжены внешними ссылками. Изображения карт не публикуются.</p>
  </footer>
  <script src="/assets/app.js?v={SCRIPT_VERSION}" defer></script>
</body>
</html>
"""


def breadcrumb(items: list[tuple[str, str | None]]) -> str:
    parts = []
    for label, href in items:
        parts.append(f'<a href="{href}">{esc(label)}</a>' if href else f"<span>{esc(label)}</span>")
    return '<nav class="breadcrumbs" aria-label="Хлебные крошки">' + "<span>/</span>".join(parts) + "</nav>"


def chips(items: list[dict[str, object]], *, kind: str | None = None) -> str:
    values = []
    for item in items:
        label = esc(item.get("title") or item.get("slug"))
        if kind and item.get("slug"):
            values.append(f'<a class="chip" href="{route(kind, str(item["slug"]))}">{label}</a>')
        else:
            values.append(f'<span class="chip">{label}</span>')
    return '<div class="chips">' + "".join(values) + "</div>" if values else ""


def external_links(items: list[dict[str, object]]) -> str:
    links = []
    for item in items:
        target = str(item.get("url", ""))
        if urlsplit(target).scheme not in ("http", "https"):
            continue
        label = item.get("title") or target
        links.append(
            f'<li><a href="{esc(target)}" rel="noopener noreferrer" target="_blank">{esc(label)}</a></li>'
        )
    return '<ul class="source-list">' + "".join(links) + "</ul>" + link_health([str(item.get("url", "")) for item in items]) if links else ""


def comments_section(comments: list[dict[str, object]]) -> str:
    if not comments:
        return ""
    rendered = []
    for comment in comments:
        meta = " · ".join(value for value in (str(comment.get("author", "")), str(comment.get("created_at", ""))) if value)
        rendered.append(
            '<article class="comment-card">'
            f'<h3>{esc(comment.get("title") or "Комментарий")}</h3>'
            f'<p class="meta">{esc(meta)}</p>{text_blocks(str(comment.get("text", "")))}</article>'
        )
    return f'<section class="detail-section"><h2>Комментарии из архива</h2>{"".join(rendered)}</section>'


def render_home(counts: dict[str, int]) -> str:
    metrics = (("cards", "карточек"), ("sets", "наборов"), ("faq", "материалов FAQ"), ("faq_groups", "тем FAQ"))
    metric_html = "".join(
        f'<div><strong>{counts[key]:,}</strong><span>{label}</span></div>'.replace(",", "\u202f")
        for key, label in metrics
    )
    folders = (
        ("sets", "Наборы", "Базовые игры, дополнения, бустеры и промо", "gold"),
        ("cards", "Карточки", "Названия, особенности применения и эрраты", "blue"),
        ("faq", "FAQ", "Вопросы и ответы по правилам, собранные по темам", "red"),
    )
    folder_html = "".join(
        f'<a class="archive-folder accent-{color}" href="/{key}/">'
        f'<span class="archive-folder-tab" aria-hidden="true">{number:02d} / АРХИВ</span>'
        f'<span class="archive-folder-count">{counted(counts[key], "запись", "записи", "записей")}</span>'
        f'<strong>{title}</strong><span class="archive-folder-description">{description}</span>'
        '<span class="archive-folder-action">Открыть папку <span aria-hidden="true">↗</span></span></a>'
        for number, (key, title, description, color) in enumerate(folders, 1)
    )
    body = f"""<section class="hero archive-hero">
<p class="eyebrow">Ваш настольный справочник</p><h1>Архив правил<br>Munchkin</h1>
<p class="lead">Найдите нужную карту, откройте свой набор или разберитесь в спорной ситуации.</p>
<form class="hero-search" action="/search/">
<label class="sr-only" for="home-query">Поиск по справочнику</label>
<input id="home-query" name="q" type="search" placeholder="Например: Следопыт, смывка, проклятие…">
<button type="submit">Найти</button></form></section>
<section class="metrics" aria-label="Статистика архива">{metric_html}</section>
<nav class="archive-folders" aria-label="Разделы архива">{folder_html}</nav>"""
    return shell("", body)


def render_contacts() -> str:
    body = f"""{breadcrumb([("Главная", "/"), ("Контакты", None)])}
<header class="page-heading"><p class="eyebrow">Обратная связь</p><h1>Контакты</h1>
<p>Нашли ошибку в правилах, неработающую ссылку или хотите предложить дополнение? Напишите нам.</p></header>
<div class="narrow-content"><section class="detail-section prose">
<h2>Написать на почту</h2>
<p><a href="mailto:xpycteem1998@gmail.com">xpycteem1998@gmail.com</a></p>
<p>Если сообщаете об ошибке, приложите ссылку на страницу и, по возможности, источник с правильным правилом. Это поможет быстрее проверить и исправить информацию.</p>
</section></div>"""
    return shell("Контакты", body, active="contacts")


def render_index(title: str, intro: str, rows: list[str], *, active: str, count: int) -> str:
    body = f"""{breadcrumb([("Главная", "/"), (title, None)])}
<header class="page-heading"><p class="eyebrow">{counted(count, "запись", "записи", "записей")}</p><h1>{esc(title)}</h1><p>{esc(intro)}</p></header>
<div class="list-tools"><label for="list-filter">Фильтр списка</label><input id="list-filter" type="search" data-list-filter placeholder="Начните вводить название…"><span data-list-count>{count:,}</span></div>
<div class="entity-list" data-filter-list>{''.join(rows)}</div>"""
    return shell(title, body, active=active)


def render_redirect_page(title: str, target: str, label: str = "Открыть FAQ") -> str:
    body = f"""{breadcrumb([("Главная", "/"), (title, None)])}
<section class="not-found"><p class="eyebrow">Адрес сохранён</p><h1>{esc(title)}</h1>
<p>Материал перенесён в тематический FAQ.</p><a class="button" href="{esc(target)}">{esc(label)}</a></section>"""
    return shell(title, body, active="faq", redirect_to=target)


def validate_faq_groups(
    groups: list[dict[str, object]],
    rulings: list[dict[str, object]],
    faq_pages: list[dict[str, object]],
) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]]:
    ruling_group: dict[str, dict[str, object]] = {}
    page_group: dict[str, dict[str, object]] = {}
    errors = []
    for group in groups:
        group_id = str(group.get("id", ""))
        if not group_id or not str(group.get("title", "")).strip():
            errors.append("FAQ group without id or title")
        for slug in group.get("rulings", []):
            slug = str(slug)
            if slug in ruling_group:
                errors.append(f"ruling assigned to several FAQ groups: {slug}")
            ruling_group[slug] = group
        for slug in group.get("pages", []):
            slug = str(slug)
            if slug in page_group:
                errors.append(f"page assigned to several FAQ groups: {slug}")
            page_group[slug] = group

    expected_rulings = {str(item["slug"]) for item in rulings}
    expected_pages = {page_slug(str(item["page_key"])) for item in faq_pages}
    if set(ruling_group) != expected_rulings:
        errors.append(
            "FAQ ruling groups differ from visible rulings: "
            f"missing={sorted(expected_rulings - set(ruling_group))}, "
            f"unknown={sorted(set(ruling_group) - expected_rulings)}"
        )
    if set(page_group) != expected_pages:
        errors.append(
            "FAQ page groups differ from FAQ pages: "
            f"missing={sorted(expected_pages - set(page_group))}, "
            f"unknown={sorted(set(page_group) - expected_pages)}"
        )
    if errors:
        raise RuntimeError("; ".join(errors))
    return ruling_group, page_group


def render_faq_index(
    groups: list[dict[str, object]],
    entries_by_group: dict[str, list[tuple[str, str]]],
    total: int,
) -> str:
    rendered_groups = []
    for group in groups:
        group_id = str(group["id"])
        entries = entries_by_group.get(group_id, [])
        if not entries:
            continue
        rows = [row for _, row in sorted(entries, key=lambda item: item[0])]
        rendered_groups.append(
            f'<details class="set-family faq-family" id="{esc(group_id)}" data-filter-group'
            f'{" open" if not rendered_groups else ""}>'
            f'<summary><span class="folder-mark" aria-hidden="true"></span><strong>{esc(group["title"])}</strong>'
            f'<small>{counted(len(rows), "материал", "материала", "материалов")}</small></summary><div class="set-family-content">'
            f'<p class="faq-group-description">{esc(group.get("description", ""))}</p>'
            f'<div class="entity-list faq-entity-list">{"".join(rows)}</div></div></details>'
        )
    body = f"""{breadcrumb([("Главная", "/"), ("FAQ", None)])}
<header class="page-heading"><p class="eyebrow">{counted(total, "материал", "материала", "материалов")}</p><h1>FAQ</h1>
<p>Вопросы, ответы и восстановленные правила собраны в тематические разделы.</p></header>
<div class="list-tools"><label for="list-filter">Фильтр FAQ</label><input id="list-filter" type="search" data-list-filter placeholder="Вопрос, карта или правило…"><span data-list-count>{total}</span></div>
<div class="set-families faq-families" data-filter-list>{''.join(rendered_groups)}</div>"""
    return shell("FAQ", body, active="faq")


def menu_slugs(items: list[dict[str, object]]) -> list[str]:
    output = []
    for item in items:
        output.append(str(item["slug"]))
        output.extend(menu_slugs(list(item.get("children", []))))
    return output


def set_card_count(item: dict[str, object]) -> int:
    return int(item.get("declared_card_count") or len(item.get("cards", [])))


def menu_group_has_sets(item: dict[str, object], set_lookup: dict[str, dict[str, object]]) -> bool:
    current = set_lookup.get(str(item["slug"]))
    return bool(current and set_card_count(current)) or any(
        menu_group_has_sets(child, set_lookup) for child in item.get("children", [])
    )


def merge_set_aliases(
    items: list[dict[str, object]], aliases: dict[str, str]
) -> dict[str, dict[str, object]]:
    lookup = {
        str(item["slug"]): {**item, "cards": list(item.get("cards", [])), "comments": list(item.get("comments", []))}
        for item in items
    }
    for alias, target in aliases.items():
        source = lookup.get(alias)
        destination = lookup.get(target)
        if source is None or destination is None:
            continue
        known_cards = {str(card["slug"]) for card in destination["cards"]}
        destination["cards"].extend(
            card for card in source["cards"] if str(card["slug"]) not in known_cards
        )
    return lookup


def normalized_card_title(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in value if character.isalnum())


def apply_numbered_catalog(
    groups: list[dict[str, object]],
    set_lookup: dict[str, dict[str, object]],
    catalog: dict[str, object],
    cards: list[dict[str, object]],
) -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
    groups = copy.deepcopy(groups)
    set_lookup = copy.deepcopy(set_lookup)
    card_candidates: dict[str, list[str]] = defaultdict(list)
    for card in cards:
        title_en = str(card.get("title_en", ""))
        if title_en:
            card_candidates[normalized_card_title(title_en)].append(str(card["slug"]))

    numbered_items = list(catalog.get("sets", []))
    for metadata in numbered_items:
        slug = str(metadata["slug"])
        existing = set_lookup.get(slug)
        if existing is None:
            existing = {"slug": slug, "cards": [], "comments": []}
            set_lookup[slug] = existing
        for key, value in metadata.items():
            if key == "cards" and existing.get("cards"):
                continue
            if key == "title" and existing.get("title"):
                continue
            existing[key] = copy.deepcopy(value)
        if metadata.get("cards"):
            for card in existing.get("cards", []):
                matches = card_candidates.get(normalized_card_title(str(card.get("title_en", ""))), [])
                if len(matches) == 1:
                    card["slug"] = matches[0]

    base_group = next((group for group in groups if group.get("slug") == "munchkin"), None)
    if base_group is None:
        raise RuntimeError("archived set menu has no Munchkin group")
    children = list(base_group.get("children", []))
    known = {str(child.get("slug", "")) for child in children}
    additions = [
        {"slug": str(item["slug"]), "label": str(item["title"]), "title": str(item["title"])}
        for item in numbered_items
        if str(item["slug"]) != str(base_group["slug"])
        and str(item["slug"]) not in known
        and set_card_count(set_lookup[str(item["slug"])])
    ]
    insert_at = next(
        (index for index, child in enumerate(children) if child.get("children")), len(children)
    )
    children[insert_at:insert_at] = additions
    base_group["children"] = children
    return groups, set_lookup


def render_set_tile(item: dict[str, object], label: str = "") -> str:
    title = str(item.get("title") or label or item["slug"])
    count = set_card_count(item)
    series_label = str(item.get("series_label", ""))
    localized = str(item.get("title_ru", ""))
    search_value = " ".join((title, localized, label, series_label, str(item["slug"])))
    count_label = counted(count, "запись", "записи", "записей") + " в архиве" if not item.get("coverage") else counted(count, "карта", "карты", "карт") + (" · список издателя" if item.get("coverage") == "official_card_list" else " · описание издателя")
    number_badge = f'<b class="set-number">{esc(series_label)}</b>' if series_label else ""
    return (
        f'<a class="set-tile" href="{route("sets", str(item["slug"]))}" '
        f'data-filter-item data-search="{esc(search_value)}">'
        f'{number_badge}'
        f'<strong>{esc(localized or title)}</strong>' + (f'<span>{esc(title)}</span>' if localized and localized != title else '') + f'<span>{esc(count_label)}</span></a>'
    )


def render_sets_index(
    groups: list[dict[str, object]],
    set_lookup: dict[str, dict[str, object]],
) -> str:
    rendered_groups = []
    visible_total = 0
    for group in groups:
        content_blocks = []
        pending_tiles = []
        group_count = 0
        base = set_lookup.get(str(group["slug"]))
        if base and set_card_count(base):
            pending_tiles.append(render_set_tile(base, str(group.get("title") or group.get("label", ""))))
            group_count += 1
        for child in group.get("children", []):
            grandchildren = list(child.get("children", []))
            if grandchildren:
                if pending_tiles:
                    content_blocks.append(f'<div class="set-catalog-grid">{"".join(pending_tiles)}</div>')
                    pending_tiles = []
                nested_tiles = []
                nested_item = set_lookup.get(str(child["slug"]))
                if nested_item and set_card_count(nested_item):
                    nested_tiles.append(render_set_tile(nested_item, str(child.get("label", ""))))
                for grandchild in grandchildren:
                    item = set_lookup.get(str(grandchild["slug"]))
                    if item and set_card_count(item):
                        nested_tiles.append(render_set_tile(item, str(grandchild.get("label", ""))))
                if nested_tiles:
                    group_count += len(nested_tiles)
                    content_blocks.append(
                        '<section class="set-subfolder" data-filter-subgroup>'
                        f'<h3><span class="folder-mark" aria-hidden="true"></span>{esc(child.get("title") or child.get("label") or "Другие")}</h3>'
                        f'<div class="set-catalog-grid">{"".join(nested_tiles)}</div></section>'
                    )
            else:
                item = set_lookup.get(str(child["slug"]))
                if item and set_card_count(item):
                    pending_tiles.append(render_set_tile(item, str(child.get("label", ""))))
                    group_count += 1
        if pending_tiles:
            content_blocks.append(f'<div class="set-catalog-grid">{"".join(pending_tiles)}</div>')
        if not group_count:
            continue
        visible_total += group_count
        title = str(group.get("title") or group.get("label") or group["slug"])
        rendered_groups.append(
            f'<details class="set-family" data-filter-group{" open" if not rendered_groups else ""}>'
            f'<summary><span class="folder-mark" aria-hidden="true"></span><strong>{esc(title)}</strong>'
            f'<small>{counted(group_count, "набор", "набора", "наборов")}</small></summary><div class="set-family-content">'
            f'{"".join(content_blocks)}</div></details>'
        )
    trail = breadcrumb([("Главная", "/"), ("Наборы", None)])
    body = f"""{trail}
<header class="page-heading"><p class="eyebrow">{counted(visible_total, "набор", "набора", "наборов")}</p><h1>Наборы</h1><p>Базовые игры, дополнения, бустеры и промо в структуре оригинального сайта. Число записей архива не равно числу физических карт в коробке.</p></header>
<div class="list-tools"><label for="list-filter">Фильтр наборов</label><input id="list-filter" type="search" data-list-filter placeholder="Название набора…"><span data-list-count>{visible_total}</span></div>
<div class="set-families" data-filter-list>{''.join(rendered_groups)}</div>"""
    return shell("Наборы", body, active="sets")


def render_card(
    card: dict[str, object],
    comments: list[dict[str, object]],
    ruling_routes: dict[str, str],
    set_lookup: dict[str, dict[str, object]],
    set_aliases: dict[str, str],
) -> str:
    page = card.get("page") or {}
    title_ru = str(card.get("title_ru", ""))
    title_en = str(card.get("title_en", ""))
    title = title_ru or title_en or str(card["slug"])
    subtitle = title_en if title_ru and title_en else ""
    status = card.get("recovery_status")
    status_label = "Есть пояснения" if card_has_notes(card) else "Название и наборы" if status == "full_page" else "Восстановлена из списка набора"
    external_refs = list(page.get("external_refs", []))
    proofs = ProofLinker(page.get("inline_external_refs"), external_refs, page)
    sections = []
    for key, heading in (
        ("specials_text", "Особенности применения"),
        ("faq_text", "FAQ карточки"),
        ("facts_text", "Факты"),
        ("errata_ru", "Эррата на русском"),
        ("errata_en", "Errata in English"),
        ("changes_19_text", "Изменения 19-го издания"),
    ):
        value = str(page.get(key, ""))
        if value.strip():
            content = render_qa(value, proofs, key) if key == "faq_text" else proofs.render(value, key)
            sections.append(f'<section class="detail-section"><h2>{heading}</h2><div class="prose">{content}</div></section>')
    ruling_links = []
    for item in page.get("ruling_refs", []):
        slug = str(item.get("slug", ""))
        if slug in ruling_routes:
            ruling_links.append(f'<li><a href="{ruling_routes[slug]}">{esc(item.get("title") or slug)}</a></li>')
    if ruling_links:
        sections.append(f'<section class="detail-section"><h2>По теме в FAQ</h2><ul class="link-list">{"".join(ruling_links)}</ul></section>')
    sources = external_links([item for item in external_refs if str(item.get("url", "")) not in proofs.used_urls])
    if sources:
        sections.append(f'<section class="detail-section"><h2>Официальные ответы и источники</h2>{sources}</section>')
    if not sections:
        sections.append('<section class="empty-state"><h2>Отдельных пояснений в архиве нет</h2><p>Сохранены название и принадлежность к наборам. Поиск может найти общие правила по связанным терминам.</p></section>')
    alt_names = list(page.get("alternate_names", []))
    card_sets = []
    seen_sets = set()
    for item in card.get("sets", []):
        slug = set_aliases.get(str(item.get("slug", "")), str(item.get("slug", "")))
        if slug in set_lookup and slug not in seen_sets:
            card_sets.append({"slug": slug, "title": set_lookup[slug].get("title") or item.get("title") or slug})
            seen_sets.add(slug)
    metadata = f"""
<aside class="metadata-card">
  <div><span>Статус</span><strong>{status_label}</strong></div>
  <div><span>Наборы</span>{chips(card_sets, kind="sets") or '<em>Не указаны</em>'}</div>
  <div><span>Колода</span>{chips(list(page.get("decks", []))) or '<em>Не указана</em>'}</div>
  <div><span>Метки</span>{chips(list(page.get("tags", []))) or '<em>Нет</em>'}</div>
  {f'<div><span>Другие названия</span><p>{esc(", ".join(map(str, alt_names)))}</p></div>' if alt_names else ''}
</aside>"""
    body = f"""{breadcrumb([("Главная", "/"), ("Карточки", "/cards/"), (title, None)])}
<header class="detail-heading"><span class="status-badge">{status_label}</span><h1>{esc(title)}</h1>{f'<p class="subtitle">{esc(subtitle)}</p>' if subtitle else ''}</header>
<div class="detail-layout"><div>{source_note(page)}{''.join(sections)}{comments_section(comments)}</div>{metadata}</div>"""
    return shell(title, body, active="cards", description=f"Правила, FAQ и эррата для карты {title}.")


def render_set(item: dict[str, object], card_slugs: set[str]) -> str:
    groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for card in item.get("cards", []):
        groups[str(card.get("section") or "Карточки")].append(card)
    group_html = []
    for section, cards in groups.items():
        rendered_cards = []
        for card in cards:
            title_ru = str(card.get("title_ru", ""))
            title_en = str(card.get("title_en", ""))
            title = title_ru or title_en or str(card.get("slug", "Карточка"))
            subtitle = f"<span>{esc(title_en)}</span>" if title_ru and title_en else ""
            search_value = f"{title_ru} {title_en}"
            slug = str(card.get("slug", ""))
            if slug in card_slugs:
                rendered_cards.append(
                    f'<a class="set-card" href="{route("cards", slug)}" data-filter-item '
                    f'data-search="{esc(search_value)}"><strong>{esc(title)}</strong>{subtitle}</a>'
                )
            else:
                rendered_cards.append(
                    f'<div class="set-card set-card-unlinked" data-filter-item '
                    f'data-search="{esc(search_value)}"><strong>{esc(title)}</strong>'
                    f'{subtitle}<span>Есть в открытом списке набора</span></div>'
                )
        links = "".join(rendered_cards)
        group_html.append(f'<section class="detail-section"><h2>{esc(section)} <small>{len(cards)}</small></h2><div class="set-card-grid">{links}</div></section>')
    title = str(item.get("title") or item["slug"])
    count = set_card_count(item)
    listed_count = len(item.get("cards", []))
    coverage = str(item.get("coverage", "archived_card_list"))
    if coverage == "official_card_list":
        status_text = "Официальный список · " + counted(count, "карта", "карты", "карт")
        coverage_note = '<section class="catalog-note"><strong>Актуальное дополнение.</strong> Названия карточек взяты из открытого списка издателя. Карточки со страницами в восстановленном архиве доступны по ссылке.</section>'
    elif coverage == "set_metadata_only":
        status_text = "По описанию издателя · " + counted(count, "карта", "карты", "карт")
        coverage_note = '<section class="catalog-note"><strong>Открытого полного списка карточек пока нет.</strong> Издатель подтверждает название и общее количество, поэтому набор показан без пустой карточной выдачи.</section>'
    else:
        status_text = "Архивный список · " + counted(count, "запись", "записи", "записей")
        coverage_note = '<p class="catalog-note">Это число восстановленных записей, а не физических карт в коробке. Повторы и разные издания могут учитываться иначе.</p>'
    sources = external_links(list(item.get("sources", [])))
    source_section = (
        f'<section class="detail-section"><h2>Открытые источники</h2>{sources}</section>'
        if sources else ""
    )
    filter_tools = (
        f'<div class="list-tools"><label for="list-filter">Фильтр карточек</label><input id="list-filter" type="search" data-list-filter placeholder="Название карточки…"><span data-list-count>{listed_count}</span></div>'
        if listed_count else ""
    )
    series_label = str(item.get("series_label", ""))
    body = f"""{breadcrumb([("Главная", "/"), ("Наборы", "/sets/"), (title, None)])}
<header class="detail-heading"><span class="status-badge">{esc(series_label + ' · ' if series_label else '')}{esc(status_text)}</span><h1>{esc(item.get('title_ru') or title)}</h1>{f'<p class="subtitle">{esc(title)}</p>' if item.get('title_ru') else ''}</header>
{coverage_note}{filter_tools}<div data-filter-list>{''.join(group_html)}</div>{source_section}"""
    return shell(title, body, active="sets")


def render_ruling(
    item: dict[str, object],
    comments: list[dict[str, object]],
    card_slugs: set[str],
    faq_group: dict[str, object],
) -> str:
    title = str(item.get("title") or item["slug"])
    card_links = []
    for ref in item.get("card_refs", []):
        slug = str(ref.get("slug", ""))
        if slug in card_slugs:
            card_links.append(f'<li><a href="{route("cards", slug)}">{esc(ref.get("title") or slug)}</a></li>')
    related = f'<section class="detail-section"><h2>Связанные карточки</h2><ul class="link-list">{"".join(card_links)}</ul></section>' if card_links else ""
    external_refs = list(item.get("external_refs", []))
    proofs = ProofLinker(item.get("inline_external_refs"), external_refs, item)
    ruling_text = render_qa(str(item.get("text", "")), proofs, "text")
    sources = external_links([item for item in external_refs if str(item.get("url", "")) not in proofs.used_urls])
    source_section = f'<section class="detail-section"><h2>Источники</h2>{sources}</section>' if sources else ""
    group_title = str(faq_group["title"])
    group_url = f'/faq/#{esc(faq_group["id"])}'
    body = f"""{breadcrumb([("Главная", "/"), ("FAQ", "/faq/"), (group_title, group_url), (title, None)])}
<header class="detail-heading"><span class="status-badge">FAQ · {esc(group_title)}</span><h1>{esc(title)}</h1></header>
<div class="narrow-content">{source_note(item)}<section class="detail-section prose">{ruling_text}</section>{related}{source_section}{comments_section(comments)}</div>"""
    return shell(title, body, active="faq")


def render_generic_page(
    item: dict[str, object],
    comments: list[dict[str, object]],
    *,
    faq_group: dict[str, object] | None = None,
    redirect_to: str = "",
) -> str:
    title = str(item.get("title") or "Страница")
    is_faq = item.get("category") == "faq"
    parent = ("FAQ", "/faq/") if is_faq else ("Материалы", "/pages/")
    external_refs = list(item.get("external_refs", []))
    proofs = ProofLinker(item.get("inline_external_refs"), external_refs, item)
    page_text = (
        render_qa(str(item.get("text", "")), proofs, "text")
        if is_faq else proofs.render(str(item.get("text", "")), "text")
    )
    sources = external_links([item for item in external_refs if str(item.get("url", "")) not in proofs.used_urls])
    source_section = f'<section class="detail-section"><h2>Источники</h2>{sources}</section>' if sources else ""
    trail = [("Главная", "/"), parent]
    if is_faq and faq_group:
        trail.append((str(faq_group["title"]), f'/faq/#{esc(faq_group["id"])}'))
    trail.append((title, None))
    body = f"""{breadcrumb(trail)}
<header class="detail-heading"><span class="status-badge">{"FAQ" if is_faq else "Архивная страница"}</span><h1>{esc(title)}</h1></header>
<div class="narrow-content">{source_note(item)}<section class="detail-section prose">{page_text}</section>{source_section}{comments_section(comments)}</div>"""
    return shell(title, body, active="faq" if is_faq else "", redirect_to=redirect_to)


def render_article_toc(item):
    targets = {"qa-" + qa["id"]: qa["question"] for qa in item["qa"]}
    targets.update({f"abilities-{n}": section["title"] for n, section in enumerate(item.get("abilities", []), 1)})
    groups = item.get("toc_groups") or [{"title": "Разделы статьи", "links": list(targets.items())}]
    anchors = [anchor for group in groups for anchor, _ in group["links"]]
    if len(anchors) != len(set(anchors)) or set(anchors) != set(targets):
        raise ValueError(f'Оглавление не соответствует разделам статьи: {item["slug"]}')
    sections = []
    for n, group in enumerate(groups, 1):
        links = ''.join(f'<li><a href="#{esc(anchor)}" title="{esc(targets[anchor])}"><span>{esc(label)}</span><span class="toc-arrow" aria-hidden="true">↗</span></a></li>' for anchor, label in group["links"])
        sections.append(f'<section class="article-toc-group"><h3><span aria-hidden="true">{n:02}</span>{esc(group["title"])}</h3><ul>{links}</ul></section>')
    return f'<nav class="detail-section article-toc" aria-labelledby="article-toc-title"><header class="article-toc-heading"><h2 id="article-toc-title">В этой статье</h2><span>Быстрый переход к разделу</span></header><div class="article-toc-grid">{"".join(sections)}</div></nav>'


def render_editorial(item):
    group = EDITORIAL["group"]
    blocks = []
    for qa in item["qa"]:
        anchor = "qa-" + qa["id"]
        source = item["sources"][qa["source"]]
        blocks.append(f'<article class="qa-item" id="{anchor}"><h2 class="qa-question"><span class="qa-marker" aria-hidden="true">В</span><span>{esc(qa["question"])} <a class="answer-link" href="#{anchor}" aria-label="Ссылка на этот ответ">#</a></span></h2><div class="qa-answer"><span class="qa-marker" aria-hidden="true">О</span><div><p>{esc(qa["answer"])} <a class="inline-proof" href="{esc(source["url"])}" target="_blank" rel="noopener noreferrer">Источник</a></p></div></div></article>')
    abilities = []
    for n, section in enumerate(item.get("abilities", []), 1):
        anchor = f"abilities-{n}"
        entries = []
        for power in section["items"]:
            source = item["sources"][power["source"]]
            entries.append(f'<div><dt>{esc(power["name"])}</dt><dd>{esc(power["text"])} <a class="ability-source" href="{esc(source["url"])}" target="_blank" rel="noopener noreferrer">Источник</a></dd></div>')
        abilities.append(f'<section class="detail-section" id="{anchor}"><h2>{esc(section["title"])}</h2><dl class="epic-abilities">{"".join(entries)}</dl></section>')
    body = breadcrumb([("Главная", "/"), ("FAQ", "/faq/"), (group["title"], "/faq/#" + group["id"]), (item["title"], None)])
    body += f'<header class="detail-heading"><span class="status-badge">Домашний режим · Исторические правила</span><h1>{esc(item["title"])}</h1><p class="lead">{esc(item.get("introduction", ""))}</p></header><div class="narrow-content"><aside class="archive-notice"><strong>Источники проверены {item["checked_at"]}</strong><p>{esc(item["notice"])}</p></aside>{render_article_toc(item)}<div class="qa-list">{"".join(blocks)}</div>{"".join(abilities)}<section class="detail-section"><h2>Источники</h2>{external_links(item["sources"])}</section></div>'
    return shell(item["title"], body, active="faq", description=group["description"])


def render_card_catalog(rows, page_number, total_pages, count):
    links = []
    for n in range(1, total_pages + 1):
        url = "/cards/" if n == 1 else f"/cards/page/{n}/"
        links.append(f'<a href="{url}"' + (' aria-current="page"' if n == page_number else '') + f'>{n}</a>')
    body = breadcrumb([("Главная", "/"), ("Карточки", None)])
    body += f'<header class="page-heading"><p class="eyebrow">{counted(count, "карточка", "карточки", "карточек")}</p><h1>Карточки</h1><p>Страница {page_number} из {total_pages}. Фильтр ищет по всему каталогу, включая другие переводы названий.</p></header>'
    body += '<section class="search-panel" data-catalog><label for="site-search">Поиск по всем карточкам</label><input id="site-search" type="search" data-site-search placeholder="Название, набор или метка…"><label class="checkbox-label"><input type="checkbox" data-notes-only> Только с пояснениями</label><p data-search-status role="status" aria-live="polite">Карточки текущей страницы</p><div class="search-results" data-search-results>' + ''.join(rows) + '</div><button class="button" data-search-more hidden type="button">Показать ещё</button><nav class="pagination" aria-label="Страницы каталога">' + ''.join(links) + '</nav></section>'
    return shell("Карточки" + (f" — страница {page_number}" if page_number > 1 else ""), body, active="cards")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    if output != DEFAULT_OUTPUT.resolve() and ROOT.resolve() not in output.parents:
        parser.error("--output must be inside the recovery project")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    (output / "assets").mkdir()
    (output / "data").mkdir()
    shutil.copy2(SOURCE / "styles.css", output / "assets" / "styles.css")
    shutil.copy2(SOURCE / "app.js", output / "assets" / "app.js")
    shutil.copy2(SOURCE / "favicon.svg", output / "assets" / "favicon.svg")

    cards = json.loads((EXPORTS / "cards.json").read_text(encoding="utf-8"))
    for card in cards:
        if card.get("page"): attach_annotations(card["page"], "card")
    all_sets = json.loads((EXPORTS / "sets.json").read_text(encoding="utf-8"))
    set_menu = json.loads((EXPORTS / "set_menu.json").read_text(encoding="utf-8"))
    numbered_catalog = json.loads((EXPORTS / "munchkin_numbered_sets.json").read_text(encoding="utf-8"))
    set_aliases = {str(key): str(value) for key, value in set_menu.get("aliases", {}).items()}
    merged_set_lookup = merge_set_aliases(all_sets, set_aliases)
    ordered_set_slugs = menu_slugs(list(set_menu.get("groups", [])))
    unexpected_sets = sorted(
        str(item["slug"])
        for item in all_sets
        if item.get("cards") and str(item["slug"]) not in ordered_set_slugs and str(item["slug"]) not in set_aliases
    )
    if unexpected_sets:
        raise RuntimeError(f"non-empty sets missing from archived menu: {', '.join(unexpected_sets)}")
    set_groups, merged_set_lookup = apply_numbered_catalog(
        list(set_menu.get("groups", [])), merged_set_lookup, numbered_catalog, cards
    )
    ordered_set_slugs = menu_slugs(set_groups)
    sets = [merged_set_lookup[slug] for slug in ordered_set_slugs if slug in merged_set_lookup and set_card_count(merged_set_lookup[slug])]
    set_lookup = {str(item["slug"]): item for item in sets}
    localizations = json.loads((SOURCE / "set_localizations.json").read_text())
    for item in sets:
        if str(item["slug"]) in localizations["titles"]:
            item["title_ru"] = localizations["titles"][str(item["slug"])]
            item.setdefault("sources", []).extend(localizations["sources"])
    visible_set_groups = sum(
        menu_group_has_sets(group, set_lookup) for group in set_groups
    )
    rulings = json.loads((EXPORTS / "rulings.json").read_text(encoding="utf-8"))
    pages = json.loads((EXPORTS / "pages.json").read_text(encoding="utf-8"))
    for item in rulings: attach_annotations(item, "ruling")
    for item in pages: attach_annotations(item, "page")
    ruling_redirects = build_ruling_redirects(rulings, pages)
    visible_rulings = [item for item in rulings if str(item["slug"]) not in ruling_redirects]
    faq_pages = [item for item in pages if item.get("category") == "faq"]
    faq_groups = json.loads(FAQ_GROUPS_FILE.read_text(encoding="utf-8"))
    ruling_group, page_group = validate_faq_groups(faq_groups, visible_rulings, faq_pages)
    faq_groups.append(EDITORIAL["group"])
    comments = json.loads((EXPORTS / "comments.json").read_text(encoding="utf-8"))
    comments_by_source: dict[str, list[dict[str, object]]] = defaultdict(list)
    for comment in comments:
        comments_by_source[str(comment.get("source_page", ""))].append(comment)
    card_slugs = {str(item["slug"]) for item in cards}
    ruling_routes = {
        str(item["slug"]): faq_ruling_route(str(item["slug"])) for item in visible_rulings
    }
    ruling_routes.update(
        {slug: item["target"] for slug, item in ruling_redirects.items()}
    )
    ROUTE_MAP.update({("/card/" + str(card["slug"])).casefold(): route("cards", str(card["slug"])) for card in cards})
    ROUTE_MAP.update({("/ruling/" + slug).casefold(): target for slug, target in ruling_routes.items()})
    ROUTE_MAP.update({("/set/" + slug).casefold(): route("sets", slug) for slug in set_lookup})
    ROUTE_MAP.update({("/set/" + alias).casefold(): route("sets", target) for alias, target in set_aliases.items() if target in set_lookup})
    ROUTE_MAP.update({normalized_page_path(str(item["page_key"])): route("faq" if item.get("category") == "faq" else "pages", page_slug(str(item["page_key"]))) for item in pages})

    counts = {
        "cards": len(cards),
        "sets": len(sets),
        "rulings": len(visible_rulings),
        "faq": len(visible_rulings) + len(faq_pages) + len(EDITORIAL["articles"]),
        "faq_pages": len(faq_pages),
        "faq_groups": len(faq_groups),
    }
    card_rows = []
    search_index = []
    for card in sorted(cards, key=lambda item: (str(item.get("title_ru", "")).casefold(), str(item.get("title_en", "")).casefold())):
        slug = str(card["slug"])
        title = str(card.get("title_ru") or card.get("title_en") or slug)
        en = str(card.get("title_en", ""))
        page = card.get("page") or {}
        names = card_names(card)
        tags = [str(item.get("title", "")) for key in ("sets", "tags", "decks") for item in (card.get(key) or page.get(key) or [])]
        tags += [str(set_lookup.get(str(item.get("slug")), {}).get("title_ru", "")) for item in card.get("sets", [])]
        search_value = " ".join(names + tags + [slug])
        card_rows.append(f'<a class="entity-row" href="{route("cards", slug)}"><strong>{esc(title)}</strong><span>{esc(en) if en != title else ""}</span><small>{"Есть пояснения" if card_has_notes(card) else "Название и наборы"}</small></a>')
        source_url = f"https://munchkindb.ru/card/{quote(slug, safe='-._~')}"
        write_page(output, "cards", slug, render_card(card, comments_by_source[source_url], ruling_routes, set_lookup, set_aliases))
        page = card.get("page") or {}
        search_index.append({"type": "Карточка", "title": title, "names": names, "notes": card_has_notes(card), "subtitle": en if en != title else "", "url": route("cards", slug), "search": " ".join([search_value] + [str(page.get(field, "")) for field in ("specials_text", "faq_text", "facts_text", "errata_ru", "errata_en", "changes_19_text")])})
    catalog_pages = (len(card_rows) + 99) // 100
    for n in range(1, catalog_pages + 1):
        write_page(output, "cards" if n == 1 else "cards/page", "" if n == 1 else str(n), render_card_catalog(card_rows[(n-1)*100:n*100], n, catalog_pages, len(cards)))

    set_rows = []
    for item in sets:
        slug = str(item["slug"])
        title = str(item.get("title") or slug)
        count = set_card_count(item)
        series_label = str(item.get("series_label", ""))
        set_rows.append(f'<a class="entity-row" href="{route("sets", slug)}" data-filter-item data-search="{esc(title + " " + series_label + " " + slug)}"><strong>{esc(title)}</strong><span>{esc(series_label)}</span><small>{count} карт</small></a>')
        write_page(output, "sets", slug, render_set(item, card_slugs))
        search_index.append({"type": "Набор", "title": str(item.get("title_ru") or title), "names": [title, str(item.get("title_ru", ""))], "subtitle": title + " · " + series_label, "url": route("sets", slug), "search": title + " " + series_label + " " + slug + " " + str(item.get("title_ru", ""))})
    write_page(output, "", "", render_home(counts))
    write_page(output, "sets", "", render_sets_index(set_groups, set_lookup))

    faq_entries_by_group: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for item in EDITORIAL["articles"]:
        target = route("faq", item["slug"])
        text = " ".join([item.get("introduction", "")] + [qa["question"] + " " + qa["answer"] for qa in item["qa"]] + [power["name"] + " " + power["text"] for section in item.get("abilities", []) for power in section["items"]])
        row = f'<a class="entity-row" href="{target}" data-filter-item data-search="{esc(item["title"] + " " + text)}"><strong>{esc(item["title"])}</strong><span>До 20-го уровня, две Двери и особые случаи</span><small>Домашний режим</small></a>'
        faq_entries_by_group[EDITORIAL["group"]["id"]].append((item["title"], row))
        write_page(output, "faq", item["slug"], render_editorial(item))
        search_index.append({"type": "FAQ", "title": item["title"], "names": ["Эпический Манчкин", "Epic Munchkin"], "subtitle": EDITORIAL["group"]["description"], "url": target, "search": text + " Epic Munchkin"})
    for item in sorted(visible_rulings, key=lambda value: str(value.get("title", "")).casefold()):
        slug = str(item["slug"])
        title = str(item.get("title") or slug)
        text = str(item.get("text", ""))
        group = ruling_group[slug]
        target = faq_ruling_route(slug)
        row = f'<a class="entity-row" href="{target}" data-filter-item data-search="{esc(title + " " + text)}"><strong>{esc(title)}</strong><span>{esc(text[:150])}</span><small>правило</small></a>'
        faq_entries_by_group[str(group["id"])].append((title.casefold(), row))
        source_url = f"https://munchkindb.ru/ruling/{quote(slug, safe='-._~')}"
        write_page(output, "faq", faq_ruling_slug(slug), render_ruling(item, comments_by_source[source_url], card_slugs, group))
        search_index.append({"type": "FAQ", "title": title, "subtitle": str(group["title"]) + " · " + text[:180], "url": target, "search": title + " " + text})

    page_rows = []
    for item in pages:
        slug = page_slug(str(item["page_key"]))
        title = str(item.get("title") or slug)
        kind = "faq" if item.get("category") == "faq" else "pages"
        text = str(item.get("text", ""))
        row = f'<a class="entity-row" href="{route(kind, slug)}" data-filter-item data-search="{esc(title + " " + text)}"><strong>{esc(title)}</strong><span>{esc(text[:150])}</span><small>{"основной FAQ" if kind == "faq" else "страница"}</small></a>'
        group = page_group[slug] if kind == "faq" else None
        if group:
            faq_entries_by_group[str(group["id"])].append((title.casefold(), row))
        else:
            page_rows.append(row)
        write_page(output, kind, slug, render_generic_page(item, comments_by_source[str(item["page_key"])], faq_group=group))
        search_index.append({"type": "FAQ" if kind == "faq" else "Материал", "title": title, "subtitle": str(item.get("text", ""))[:180], "url": route(kind, slug), "search": title + " " + str(item.get("text", ""))})
    write_page(output, "faq", "", render_faq_index(faq_groups, faq_entries_by_group, counts["faq"]))
    write_page(output, "pages", "", render_index("Архивные материалы", "Другие содержательные страницы восстановленного сайта.", page_rows, active="", count=len(page_rows)))

    for item in rulings:
        slug = str(item["slug"])
        redirect = ruling_redirects.get(slug)
        target = str(redirect["target"]) if redirect else faq_ruling_route(slug)
        title = str(item.get("title") or slug)
        label = str(redirect.get("label", "Открыть FAQ")) if redirect else "Открыть в FAQ"
        write_page(output, "rulings", slug, render_redirect_page(title, target, label))
    write_page(output, "rulings", "", render_redirect_page("FAQ", "/faq/", "Открыть тематический FAQ"))

    search_body = f"""{breadcrumb([("Главная", "/"), ("Поиск", None)])}
<header class="page-heading"><p class="eyebrow">По всему справочнику</p><h1>Поиск</h1><p>Карточки, тематический FAQ и наборы в одном индексе.</p></header>
<section class="search-panel"><label for="site-search">Запрос</label><input id="site-search" type="search" data-site-search autocomplete="off" placeholder="Введите два или больше символов…"><label class="search-type-label" for="search-type">Искать в разделе</label><select id="search-type" data-search-type><option value="">Всё</option><option>Карточка</option><option>FAQ</option><option>Набор</option><option>Материал</option></select><p data-search-status role="status" aria-live="polite">Начните вводить запрос</p><div class="search-results" data-search-results></div><button class="button" data-search-more hidden type="button">Показать ещё</button><noscript>Для поиска включите JavaScript. Каталоги и страницы доступны без него.</noscript></section>"""
    write_page(output, "search", "", shell("Поиск", search_body))
    write_page(output, "contacts", "", render_contacts())
    (output / "data" / "search-index.json").write_text(json.dumps(search_index, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    (output / "404.html").write_text(shell("Страница не найдена", '<section class="not-found"><p class="eyebrow">Ошибка 404</p><h1>Страница не найдена</h1><p>Попробуйте поиск по восстановленному справочнику.</p><a class="button" href="/search/">Открыть поиск</a></section>'), encoding="utf-8")
    (output / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n", encoding="utf-8")
    index_version = hashlib.sha256((output / "data/search-index.json").read_bytes()).hexdigest()[:12]
    sitemap = []
    for filename in output.rglob("*.html"):
        content = filename.read_text()
        content = content.replace('<body>', f'<body data-search-index="/data/search-index.json?v={index_version}">')
        if filename.name == "404.html":
            content = content.replace('</head>', '<meta name="robots" content="noindex,follow"></head>')
        if 'http-equiv="refresh"' not in content and 'name="robots"' not in content:
            rel = filename.parent.relative_to(output).as_posix()
            url = SITE_URL + "/" + (quote(rel, safe="/-._~") + "/" if rel != "." else "")
            sitemap.append(f'<url><loc>{esc(url)}</loc></url>')
        filename.write_text(content)
    (output / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + ''.join(sorted(sitemap)) + '</urlset>')

    result = {
        **counts,
        "source_sets": len(all_sets),
        "source_rulings": len(rulings),
        "faq_rulings": len(visible_rulings),
        "editorial_faq": len(EDITORIAL["articles"]),
        "catalog_pages": catalog_pages,
        "ruling_redirects": len(ruling_redirects),
        "legacy_ruling_redirects": len(rulings),
        "empty_sets_removed": sum(not item.get("cards") for item in all_sets),
        "set_aliases_merged": len(set_aliases),
        "set_groups": visible_set_groups,
        "current_numbered_sets": len(numbered_catalog.get("sets", [])),
        "supplemental_sets": sum(
            str(item.get("coverage", "")).startswith("official")
            or item.get("coverage") == "set_metadata_only"
            for item in numbered_catalog.get("sets", [])
        ),
        "asset_versions": {"styles.css": STYLE_VERSION, "app.js": SCRIPT_VERSION},
        "comments": len(comments),
        "search_records": len(search_index),
        "html_pages": len(list(output.rglob("*.html"))),
        "output": output.relative_to(ROOT).as_posix(),
    }
    (output / "build-info.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
