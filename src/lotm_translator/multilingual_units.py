"""Deterministic preparation of Chinese and English corpus sources."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .text import clean_story_text


_HEADING = re.compile(r"^\s*(?:#\s*)?(?:chapter\s+\d+|第[〇一二三四五六七八九十百\d]+章)\b.*$", re.IGNORECASE)
_EN_FOOTNOTE_MARKER = re.compile(r"(?<=[A-Za-z])\d{1,2}(?=[?!.,;:])")
_ZH_SENTENCE = re.compile(r".+?(?:[。！？…]+[”’」』）】]*)|.+$", re.DOTALL)


def clean_chapter(text: str) -> str:
    """Remove chapter metadata and clearly non-story blocks, preserving prose."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    while lines and (not lines[0].strip() or _HEADING.match(lines[0])):
        lines.pop(0)
    cleaned = clean_story_text("\n".join(lines))
    cleaned = _EN_FOOTNOTE_MARKER.sub("", cleaned)
    paragraphs = []
    for block in re.split(r"\n\s*\n", cleaned):
        paragraph = " ".join(line.strip() for line in block.splitlines() if line.strip())
        if paragraph:
            paragraphs.append(paragraph)
    return "\n\n".join(paragraphs) + ("\n" if paragraphs else "")


def split_into_units(text: str, language: str) -> list[str]:
    """Create sentence-sized alignment units without changing their wording."""
    paragraphs = [item.strip() for item in text.strip().split("\n\n") if item.strip()]
    if language == "en":
        try:
            import pysbd
        except ImportError as error:  # pragma: no cover - dependency is explicit
            raise RuntimeError("pysbd is required for English units. Run: .\\.venv\\Scripts\\python.exe -m pip install pysbd") from error
        segmenter = pysbd.Segmenter(language="en", clean=False)
        return [sentence.strip() for paragraph in paragraphs for sentence in segmenter.segment(paragraph) if sentence.strip()]
    if language == "zh":
        return [sentence.strip() for paragraph in paragraphs for sentence in _ZH_SENTENCE.findall(paragraph) if sentence.strip()]
    raise ValueError(f"Unsupported language: {language}")


def prepare_chapters(raw_directory: Path, source_directory: Path, units_directory: Path, language: str, count: int = 10) -> list[dict[str, object]]:
    """Create clean chapter source and one-paragraph-per-unit derived copies."""
    files = [path for path in sorted(raw_directory.glob("*.txt")) if path.name != "manifest.json"][:count]
    if len(files) != count:
        raise ValueError(f"Expected {count} {language} chapter files in {raw_directory}, found {len(files)}")
    source_directory.mkdir(parents=True, exist_ok=True)
    units_directory.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, object]] = []
    for chapter, raw in enumerate(files, start=1):
        text = clean_chapter(raw.read_text(encoding="utf-8"))
        if not text.strip():
            raise ValueError(f"No story text remains after cleaning {raw}")
        stem = f"ch_{chapter:04d}_{language}"
        source = source_directory / f"{stem}_source.txt"
        units = units_directory / f"{stem}_units.txt"
        source.write_text(text, encoding="utf-8")
        unit_values = split_into_units(text, language)
        units.write_text("\n\n".join(unit_values) + "\n", encoding="utf-8")
        manifest.append({"chapter": chapter, "raw": str(raw), "source": str(source), "units": str(units), "paragraphs": len(text.strip().split("\n\n")), "units_count": len(unit_values)})
    return manifest


def prepare_zh_en(root: Path, count: int = 10) -> dict[str, list[dict[str, object]]]:
    root = root.resolve()
    raw = root / "data" / "raw" / "qwen_10"
    corpus = root / "data" / "corpus"
    result = {
        "zh": prepare_chapters(raw / "zh", corpus / "01_zh_source", corpus / "02_zh_units", "zh", count),
        "en": prepare_chapters(raw / "en", corpus / "03_en_source", corpus / "04_en_units", "en", count),
    }
    (corpus / "preparation_manifest.json").write_text(
        json.dumps({"status": "prepared_not_gold", "chapters": count, "languages": result}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result
