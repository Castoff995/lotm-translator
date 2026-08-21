"""Human-readable HTML for a Bertalign alignment report."""
from __future__ import annotations

import html
import json
from pathlib import Path


def _paragraphs(path: Path) -> list[str]:
    return [part.strip() for part in path.read_text(encoding="utf-8").split("\n\n") if part.strip()]


def _group_text(paragraphs: list[str], indexes: list[int]) -> str:
    return "\n\n".join(paragraphs[index - 1] for index in indexes if 0 < index <= len(paragraphs))


def render_bertalign_html(alignment: Path, left: Path, right: Path, output: Path, left_label: str = "fan_75", right_label: str = "official") -> int:
    report = json.loads(alignment.read_text(encoding="utf-8"))
    left_parts = _paragraphs(left)
    right_parts = _paragraphs(right)
    rows: list[str] = []
    for number, group in enumerate(report["alignments"], start=1):
        left_indexes = group["left"]
        right_indexes = group["right"]
        left_text = html.escape(_group_text(left_parts, left_indexes)) or "—"
        right_text = html.escape(_group_text(right_parts, right_indexes)) or "—"
        rows.append(
            f'<section class="group"><div class="meta">Группа {number} · '
            f'{html.escape(str(left_indexes))} ↔ {html.escape(str(right_indexes))}</div>'
            f'<div class="cells"><article><h2>{html.escape(left_label)}</h2><pre>{left_text}</pre></article>'
            f'<article><h2>{html.escape(right_label)}</h2><pre>{right_text}</pre></article></div></section>'
        )
    page = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Сравнение переводов</title>
<style>
body {{ margin: 0; background: #f4f4f5; color: #191919; font: 16px/1.45 system-ui, sans-serif; }}
header {{ position: sticky; top: 0; background: #1e293b; color: white; padding: 14px 22px; z-index: 1; }}
main {{ max-width: 1500px; margin: 18px auto; padding: 0 18px; }}
.group {{ background: white; margin: 12px 0; border: 1px solid #d4d4d8; border-radius: 8px; overflow: hidden; }}
.meta {{ font-size: 13px; color: #52525b; padding: 8px 12px; background: #fafafa; border-bottom: 1px solid #e4e4e7; }}
.cells {{ display: grid; grid-template-columns: 1fr 1fr; }}
article {{ min-width: 0; padding: 12px; }} article + article {{ border-left: 1px solid #d4d4d8; }}
h2 {{ margin: 0 0 8px; font-size: 15px; }} pre {{ margin: 0; white-space: pre-wrap; font: inherit; }}
@media (max-width: 800px) {{ .cells {{ grid-template-columns: 1fr; }} article + article {{ border-left: 0; border-top: 1px solid #d4d4d8; }} }}
</style></head><body><header><strong>Сравнение: {html.escape(left_label)} ↔ {html.escape(right_label)}</strong> · {len(rows)} групп</header>
<main>{''.join(rows)}</main></body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")
    return len(rows)
