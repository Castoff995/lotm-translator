"""Join two BGE pair alignments into reviewable Chinese–English–official-Russian units."""
from __future__ import annotations

import json
from pathlib import Path

from .audit import _bertalign_bridge_units


def _paragraphs(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").split("\n\n") if line.strip()]


def _join(rows: list[str], numbers: list[int]) -> str:
    return "\n\n".join(rows[index - 1] for index in numbers if 1 <= index <= len(rows))


def build_official_triples(zh: Path, en: Path, ru_units: Path, zh_en_alignment: Path, en_ru_alignment: Path, output: Path) -> dict:
    units = _bertalign_bridge_units(zh_en_alignment, en_ru_alignment)
    zh_rows, en_rows, ru_rows = _paragraphs(zh), _paragraphs(en), _paragraphs(ru_units)
    groups = [
        {"unit": index, **unit, "zh_text": _join(zh_rows, unit["zh"]), "en_text": _join(en_rows, unit["en"]), "ru_text": _join(ru_rows, unit["ru"])}
        for index, unit in enumerate(units, start=1)
    ]
    result = {"method": "bertalign_labse_english_bridge", "status": "alignment_requires_validation", "groups": groups}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
