from __future__ import annotations

import json
import re
from pathlib import Path


_CHAPTER_NUMBER = re.compile(r"(?:ch_)?0*(\d+)", re.IGNORECASE)
_ENGLISH_HEADING_NUMBER = re.compile(r"^#\s*Chapter\s+(\d+)\s*:", re.IGNORECASE)


def _indexed_text_files(directory: Path) -> dict[int, Path]:
    indexed: dict[int, Path] = {}
    for file in directory.glob("*.txt"):
        first_line = file.read_text(encoding="utf-8", errors="replace").splitlines()[0] if file.stat().st_size else ""
        match = _ENGLISH_HEADING_NUMBER.match(first_line) or _CHAPTER_NUMBER.search(file.stem)
        if match:
            indexed[int(match.group(1))] = file
    return indexed


def build_chapter_alignment(chinese_dir: Path, english_dir: Path, russian_dir: Path, output: Path) -> list[dict[str, object]]:
    """Create a chapter-level manifest; paragraph alignment is a later review step."""
    sources = {
        "zh_original": _indexed_text_files(chinese_dir),
        "en_official": _indexed_text_files(english_dir),
        "ru_fan_75": _indexed_text_files(russian_dir),
    }
    common_chapters = sorted(set.intersection(*(set(index) for index in sources.values())))
    records = []
    for number in common_chapters:
        records.append(
            {
                "chapter": number,
                "status": "chapter_matched_pending_paragraph_alignment",
                "sources": {label: str(path.as_posix()) for label, index in sources.items() for current, path in index.items() if current == number},
                "training_weight": {"zh_original": 1.0, "en_official": 1.0, "ru_fan_75": 0.35},
                "notes": "Fan Russian is a reference source only; it must not override future official Russian text.",
            }
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"chapter_count": len(records), "chapters": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return records
