"""A read-only status passport for the local translation corpus."""
from __future__ import annotations

import json
from pathlib import Path


def _first(directory: Path, pattern: str) -> Path | None:
    return next(iter(sorted(directory.glob(pattern))), None)


def build_status(root: Path, output: Path, chapter_count: int = 10) -> dict:
    raw = root / "data" / "raw"
    qwen = raw / "qwen_10"
    ocr = raw / "official_ru_apple" / "chapters"
    chapters: list[dict] = []
    for chapter in range(1, chapter_count + 1):
        ident = f"{chapter:04d}"
        zh = _first(qwen / "zh", f"{ident}_*.txt")
        en = _first(qwen / "en", f"{chapter + 4:04d}_*.txt")
        fan = qwen / "ru_fan_75" / f"ch_{ident}_ru_fan_75.txt"
        base = ocr / f"ch_{ident}_ru_official_apple"
        reviewed = base.with_name(base.name + "_reviewed.txt")
        qwen_review = base.with_name(base.name + "_qwen_review.json")
        audit_total = audit_resolved = 0
        if qwen_review.is_file():
            items = json.loads(qwen_review.read_text(encoding="utf-8")).get("items", [])
            audit_total = len(items)
            audit_resolved = sum(bool(item.get("review", {}).get("decision")) for item in items if isinstance(item, dict))
        official_ready = reviewed.is_file()
        if not zh or not en or not fan.is_file():
            next_step = "add_source_texts"
        elif not official_ready:
            next_step = "review_official_ocr"
        else:
            next_step = "align_official_ru_with_zh_en"
        chapters.append({
            "chapter": chapter,
            "zh": bool(zh), "en": bool(en), "fan_75": fan.is_file(),
            "official_ocr_reviewed": official_ready,
            "ocr_audit": {"resolved": audit_resolved, "total": audit_total},
            "next_step": next_step,
        })
    result = {"generated_from": "local_filesystem", "chapters": chapters}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
