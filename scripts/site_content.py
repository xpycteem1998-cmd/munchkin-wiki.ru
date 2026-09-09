"""Portable, source-backed content helpers for the static generator."""
import hashlib
import html
import json
import re
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "site_src" / "site_config.json").read_text())
SITE_URL = CONFIG["site_url"].rstrip("/")
_origin = urlsplit(SITE_URL)
if _origin.scheme not in ("http", "https") or not _origin.hostname or _origin.path or _origin.query or _origin.fragment or _origin.username or _origin.password:
    raise ValueError("site_url must be an HTTP(S) origin without a path or credentials")
ANNOTATIONS = json.loads((ROOT / "exports" / "text_annotations.json").read_text())
EDITORIAL = json.loads((ROOT / "site_src" / "editorial_faq.json").read_text())
LINK_STATUS_FILE = ROOT / "exports" / "external_link_status.json"
LINK_STATUS = json.loads(LINK_STATUS_FILE.read_text()) if LINK_STATUS_FILE.exists() else {}
ROUTE_MAP = {}


def local_target(url):
    from urllib.parse import unquote
    parsed = urlsplit(url)
    if parsed.hostname in ("munchkindb.ru", "www.munchkindb.ru"):
        return ROUTE_MAP.get(unquote(parsed.path).rstrip("/").casefold(), url)
    return url


def number(value):
    return f"{value:,}".replace(",", "\u202f")


def counted(value, one, few, many):
    last = value % 100
    form = many if 11 <= last <= 14 else one if last % 10 == 1 else few if 2 <= last % 10 <= 4 else many
    return f"{number(value)} {form}"


def attach_annotations(item, kind):
    key = str(item.get("page_key") or item["slug"])
    record = ANNOTATIONS.get(f"{kind}/{key}")
    if record:
        for field, spec in record["fields"].items():
            if hashlib.sha256(str(item.get(field, "")).encode()).hexdigest() != spec["sha256"]:
                raise ValueError(f"Stale annotations: {kind}/{key}/{field}; rerun export_text_annotations.py")
        item["_annotation"] = record


def source_note(item):
    record = item.get("_annotation")
    if not record: return ""
    stamp = record["capture"]
    date = f"{stamp[6:8]}.{stamp[4:6]}.{stamp[:4]}"
    archive = f'https://web.archive.org/web/{stamp}/{record["url"]}'
    obsolete = any(span["tag"] == "del" for field in record["fields"].values() for span in field["spans"])
    cancelled = "ОТМЕНЕНО" in str(item.get("text", ""))
    history = " Зачёркнутые фрагменты — отменённые формулировки оригинала." if obsolete else ""
    if cancelled: history += " В тексте есть отменённое изменение; не используйте его как действующее правило."
    return ('<aside class="archive-notice"><strong>Архивная редакция</strong>'
        f'<p>Снимок от {date} · {html.escape(record["provider"])}. Актуальность правила сегодня не подтверждена.{history}</p>'
        f'<a href="{html.escape(archive, quote=True)}" rel="noopener noreferrer" target="_blank">Найти снимок в Wayback Machine</a>'
        '<small>Доступность копии в Wayback может отличаться от сохранённого источника.</small></aside>'
        + link_health([span["url"] for field in record["fields"].values() for span in field["spans"] if span["tag"] == "a"]))


def link_health(urls):
    rows = []
    statuses = {"not_found": "Сервер ответил: страница не найдена", "blocked": "Отказ на автоматический запрос", "not_checked_host_blocked": "Не проверена: сайт блокирует автоматические запросы", "network_error": "Не удалось проверить соединение", "http_error": "Сервер вернул ошибку"}
    for url in sorted(set(urls)):
        if local_target(url).startswith("/"): continue
        key = urlunsplit(urlsplit(url)._replace(fragment=""))
        result = LINK_STATUS.get(key, {})
        label = statuses.get(result.get("status"))
        if not label: continue
        stamp = str(result.get("checked_at", ""))[:10]
        host = html.escape(urlsplit(url).hostname or url)
        rows.append(f'<li><a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{host}</a>: {label} ({stamp}). <a href="https://web.archive.org/web/*/{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">Поиск архивной копии</a></li>')
    if not rows: return ""
    return '<details class="source-health"><summary>Проверка внешних источников: есть ограничения</summary><p>Это результат автоматического запроса, а не проверка правильности правила. Ссылка может открываться в браузере. Наличие архивной копии не гарантируется.</p><ul>' + ''.join(rows) + '</ul></details>'


def feedback(path):
    body = "Страница: " + SITE_URL + path + "\n\nЧто нужно исправить:\n\nИсточник:\n"
    return '<p class="feedback"><a href="mailto:' + CONFIG["contact_email"] + '?subject=' + quote("Ошибка в Munchkin Wiki") + '&amp;body=' + quote(body) + '">Сообщить об ошибке на этой странице</a></p>'


def annotated_inline(value, start, spans, used_urls, mark_missing=True):
    """Render a slice with original span offsets, never zip links to proof labels."""
    end = start + len(value)
    selected = [s for s in spans if s["start"] < end and s["end"] > start]
    for marker in re.finditer(r"\[\s*пруф(?:ы)?\b[^\]\r\n]*\]", value, re.I):
        left, right = start + marker.start(), start + marker.end()
        contained = [s for s in selected if s["tag"] == "a" and left <= s["start"] and s["end"] <= right]
        if len(contained) == 1:
            original = contained[0]
            selected = [{**s, "start": left, "end": right} if s is original else s for s in selected]
    boundaries = sorted({start, end} | {max(start, s["start"]) for s in selected} | {min(end, s["end"]) for s in selected})
    output, active = [], []
    def opening(span):
        tag = span["tag"]
        if tag != "a": return f'<{tag}>'
        url = span["url"]
        used_urls.add(url)
        target = local_target(url)
        extra = ' target="_blank" rel="noopener noreferrer"' if not target.startswith("/") else ""
        return f'<a class="inline-proof" href="{html.escape(target, quote=True)}"{extra}>'
    for left, right in zip(boundaries, boundaries[1:]):
        current = [s for s in selected if s["start"] <= left and s["end"] >= right and (s["tag"] in ("del", "strong", "em") or (s["tag"] == "a" and urlsplit(s.get("url", "")).scheme in ("http", "https")))]
        # Invalid nested links from old HTML must not become nested anchors.
        # Inner anchors win where malformed original anchors overlap. This keeps
        # two adjacent proof labels attached to their own URLs (Duct Tape).
        links = [s for s in current if s["tag"] == "a"]
        current = [s for s in current if s["tag"] != "a" or s is links[-1]]
        common = 0
        while common < min(len(active), len(current)) and active[common] is current[common]: common += 1
        output.extend(f'</{s["tag"]}>' for s in reversed(active[common:]))
        output.extend(opening(s) for s in current[common:])
        piece = html.escape(value[left-start:right-start], quote=True)
        if mark_missing and not any(s["tag"] == "a" for s in current):
            def marker(match):
                label = match.group()
                if "текст карты" in label or "правила игры" in label:
                    label = re.sub(r"^\[\s*пруф\s*[-:]?\s*", "", label, flags=re.I).rstrip("]")
                    return f'<span class="source-reference">Источник: {label} (без ссылки в оригинале)</span>'
                return '<span class="missing-proof" title="В исходнике нет однозначной ссылки">Источник не восстановлен</span>'
            piece = re.sub(r"\[\s*пруф(?:ы)?\b[^\]\r\n]*\]", marker, piece, flags=re.I)
        output.append(piece)
        active = current
    output.extend(f'</{s["tag"]}>' for s in reversed(active))
    return "".join(output)


def card_has_notes(card):
    page = card.get("page") or {}
    return any(str(page.get(field, "")).strip() for field in ("specials_text", "faq_text", "facts_text", "errata_ru", "errata_en", "changes_19_text")) or bool(page.get("ruling_refs"))


def card_names(card):
    page = card.get("page") or {}
    return list(dict.fromkeys(str(name) for name in (card.get("title_ru"), card.get("title_en"), *page.get("alternate_names", [])) if name))
