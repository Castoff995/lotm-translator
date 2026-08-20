"""Run an isolated Bertalign comparison without changing the corpus pipeline."""

from __future__ import annotations

import json
from pathlib import Path

from bertalign import Bertalign
import bertalign.aligner as aligner_module


def paragraphs(path: Path) -> list[str]:
    return [item.strip() for item in path.read_text(encoding="utf-8").split("\n\n") if item.strip()]


left = Path("data/raw/qwen_10/en/0005_Chapter 1_ Crimson.txt")
right = Path("data/raw/qwen_10/ru_fan_75/ch_0001_ru_fan_75.txt")
output = Path("data/processed/bertalign_candidate_ch1_en_ru.json")

source, target = paragraphs(left), paragraphs(right)
# Bertalign's built-in language detector calls a public Google service even
# when paragraph boundaries and languages are already known locally.
aligner_module.detect_lang = lambda text: "ru" if any("а" <= char.lower() <= "я" for char in text) else "en"
aligner = Bertalign("\n".join(source), "\n".join(target), max_align=5, is_split=True)
aligner.align_sents()
rows = [
    {"left": [int(index) + 1 for index in source_indexes], "right": [int(index) + 1 for index in target_indexes]}
    for source_indexes, target_indexes in aligner.result
]
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps({"method": "bertalign_labse", "left_paragraphs": len(source), "right_paragraphs": len(target), "alignments": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Bertalign wrote {len(rows)} groups to {output}")
