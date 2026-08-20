"""Run the initial three chapters with Bertalign and publish simple progress."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.lotm_translator.bertalign_adapter import align_pair


ROOT = Path("data") / "raw" / "qwen_10"
OUTPUT = Path("data") / "processed" / "bertalign_first3"
FILES = [
    ("0001_第一章 绯红.txt", "0005_Chapter 1_ Crimson.txt", "ch_0001_ru_fan_75.txt"),
    ("0002_第二章 情况.txt", "0006_Chapter 2_ Situation.txt", "ch_0002_ru_fan_75.txt"),
    ("0003_第三章 梅丽莎（第一更求推荐票）.txt", "0007_Chapter 3_ Melissa.txt", "ch_0003_ru_fan_75.txt"),
]


def progress(**data: object) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "progress.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


for index, (zh_name, en_name, ru_name) in enumerate(FILES, 1):
    progress(status="running", chapter=index, pair="zh_en", total_chapters=len(FILES))
    align_pair(ROOT / "zh" / zh_name, ROOT / "en" / en_name, OUTPUT / "zh_en" / f"ch_{index:04d}_zh_en.json")
    progress(status="running", chapter=index, pair="en_ru", total_chapters=len(FILES))
    align_pair(ROOT / "en" / en_name, ROOT / "ru_fan_75" / ru_name, OUTPUT / "en_ru" / f"ch_{index:04d}_en_ru.json")

progress(status="complete", chapters=len(FILES))
