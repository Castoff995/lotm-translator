"""Run Qwen audit for the first three Bertalign-aligned chapters."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.lotm_translator.audit import audit_bertalign_fan_translation


audit_bertalign_fan_translation(
    Path("data/raw/qwen_10/zh"),
    Path("data/raw/qwen_10/en"),
    Path("data/raw/qwen_10/ru_fan_75"),
    Path("data/processed/bertalign_first3/zh_en"),
    Path("data/processed/bertalign_first3/en_ru"),
    Path("data/processed/fan_75_audit_bertalign_first3"),
    "qwen3:14b",
    3,
)
