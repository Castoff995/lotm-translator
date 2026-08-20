"""Render technical alignments into a human-readable comparison report."""
from __future__ import annotations

import json
import html
from pathlib import Path


def _paragraphs(path: Path) -> list[str]:
    return [item.strip() for item in path.read_text(encoding="utf-8").split("\n\n") if item.strip() and not item.startswith("# ")]


def _join(rows: list[str], indexes: list[int]) -> str:
    return "\n\n".join(rows[index - 1] for index in indexes if 1 <= index <= len(rows))


def render_comparison(alignment: Path, bge_qa: Path, fan: Path, official_units: Path, output_json: Path, output_markdown: Path) -> dict:
    rows = json.loads(alignment.read_text(encoding="utf-8")).get("alignments", [])
    scores = {item.get("group"): item for item in json.loads(bge_qa.read_text(encoding="utf-8")).get("groups", [])}
    fan_rows, official_rows = _paragraphs(fan), _paragraphs(official_units)
    groups = []
    markdown = ["# Сверка fan_75 и официального перевода — глава 1\n"]
    for number, row in enumerate(rows, start=1):
        left = row.get("left", []); right = row.get("right", [])
        score = scores.get(number, {})
        group = {"group": number, "fan_75": left, "official": right, "similarity": score.get("similarity"), "status": score.get("status", "unknown"), "fan_75_text": _join(fan_rows, left), "official_text": _join(official_rows, right)}
        groups.append(group)
        markdown.extend([f"## Группа {number} — {group['status']} — BGE: {group['similarity']}", f"fan_75 [{', '.join(map(str, left)) or 'нет'}]:\n\n{group['fan_75_text'] or '—'}", f"Официальный [{', '.join(map(str, right)) or 'нет'}]:\n\n{group['official_text'] or '—'}\n"])
    result = {"status": "readable_manual_comparison", "groups": groups}
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_markdown.write_text("\n".join(markdown), encoding="utf-8")
    return result


def render_comparison_html(source_json: Path, output_html: Path) -> int:
    groups = json.loads(source_json.read_text(encoding="utf-8")).get("groups", [])
    cards = []
    for group in groups:
        score = "—" if group.get("similarity") is None else str(group["similarity"])
        status = group.get("status", "unknown")
        cards.append(
            f'<section class="group {html.escape(status)}"><h2>Группа {group.get("group")} · BGE: {score} · {html.escape(status)}</h2>'
            f'<div class="columns"><article><h3>fan_75 · абзацы {html.escape(str(group.get("fan_75", [])))}</h3><pre>{html.escape(group.get("fan_75_text") or "—")}</pre></article>'
            f'<article><h3>Официальный · абзацы {html.escape(str(group.get("official", [])))}</h3><pre>{html.escape(group.get("official_text") or "—")}</pre></article></div></section>'
        )
    page = """<!doctype html><html lang="ru"><meta charset="utf-8"><title>LOTM — fan_75 и официальный</title>
<style>body{margin:0;background:#15191d;color:#e7edf2;font:16px system-ui;padding:24px}h1{margin-top:0}.group{border:1px solid #39434d;border-radius:10px;margin:16px 0;padding:14px;background:#20262c}.group.review{border-color:#a88036}h2{font-size:16px;margin:0 0 12px}.columns{display:grid;grid-template-columns:1fr 1fr;gap:14px}article{background:#12171b;border-radius:7px;padding:12px;min-width:0}h3{font-size:14px;color:#9fc8ec;margin:0 0 10px}pre{white-space:pre-wrap;font:inherit;line-height:1.5;margin:0}@media(max-width:850px){.columns{grid-template-columns:1fr}}</style>
<h1>Глава 1: fan_75 слева · официальный перевод справа</h1>""" + "\n".join(cards) + "</html>"
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(page, encoding="utf-8")
    return len(cards)
