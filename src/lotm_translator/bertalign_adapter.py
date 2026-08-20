"""Local Bertalign wrapper for paragraph-level corpus alignment."""

from __future__ import annotations

import json
import os
from pathlib import Path


def _paragraphs(path: Path) -> list[str]:
    return [item.strip() for item in path.read_text(encoding="utf-8").split("\n\n") if item.strip() and not item.startswith("# ")]


def _detect_local_language(text: str) -> str:
    if any("\u4e00" <= char <= "\u9fff" for char in text):
        return "zh"
    if any("а" <= char.lower() <= "я" for char in text):
        return "ru"
    return "en"


def align_pair(left_path: Path, right_path: Path, output: Path, max_align: int = 5) -> dict[str, object]:
    # Bertalign's stock detector calls Google Translate. The corpus language is
    # detectable locally, so prevent both that request and Hugging Face checks.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from bertalign import Bertalign
    import bertalign.aligner as aligner_module

    aligner_module.detect_lang = _detect_local_language
    left, right = _paragraphs(left_path), _paragraphs(right_path)
    aligner = Bertalign("\n".join(left), "\n".join(right), max_align=max_align, is_split=True)
    aligner.align_sents()
    records = [
        {"left": [int(index) + 1 for index in left_indexes], "right": [int(index) + 1 for index in right_indexes]}
        for left_indexes, right_indexes in aligner.result
    ]
    report = {
        "method": "bertalign_labse_two_pass",
        "left_file": left_path.name,
        "right_file": right_path.name,
        "left_paragraphs": len(left),
        "right_paragraphs": len(right),
        "coverage": {"left": sum(len(row["left"]) for row in records), "right": sum(len(row["right"]) for row in records)},
        "alignments": records,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
