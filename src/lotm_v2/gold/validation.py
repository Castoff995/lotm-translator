"""Strict validation of manually edited gold chapters."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from ..domain import Chapter, Language, ParagraphId, ParagraphType
from .model import GoldAlignmentSide, GoldChapter, alignment_unit_id


@dataclass(frozen=True)
class GoldValidationError(ValueError):
    issues: tuple[str, ...]

    def __str__(self) -> str:
        return "Gold validation failed:\n- " + "\n- ".join(self.issues)


def _side_items(side: GoldAlignmentSide) -> tuple[ParagraphId, ...]:
    return side.paragraphs if not side.gap else ()


def collect_gold_issues(gold: GoldChapter, chapters: tuple[Chapter, ...]) -> tuple[str, ...]:
    issues: list[str] = []
    if any(chapter.id != gold.chapter for chapter in chapters):
        issues.append("Every normalized source must match the gold chapter ID")
    chapter_by_source = {str(chapter.source): chapter for chapter in chapters}
    if len(chapter_by_source) != len(chapters):
        issues.append("Normalized source IDs must be unique")
    declared_sources = {str(source.source_id): source for source in gold.sources}
    if set(declared_sources) != set(chapter_by_source):
        issues.append("Gold source references and supplied normalized chapters differ")
    for source_id, chapter in chapter_by_source.items():
        declared = declared_sources.get(source_id)
        if declared and declared.language != chapter.language:
            issues.append(f"Source language mismatch for {source_id}")

    paragraph_by_id = {str(paragraph.id): paragraph for chapter in chapters for paragraph in chapter.paragraphs}
    expected_story = {
        str(paragraph.id) for chapter in chapters for paragraph in chapter.paragraphs
        if paragraph.paragraph_type in {ParagraphType.STORY, ParagraphType.DIALOGUE}
    }
    used: Counter[str] = Counter()
    last_index = {Language.ZH: 0, Language.EN: 0, Language.RU: 0}
    unit_ids: list[str] = []
    for unit_index, unit in enumerate(gold.alignment_units, start=1):
        expected_unit_id = alignment_unit_id(gold.chapter, unit_index)
        if unit.id != expected_unit_id:
            issues.append(f"Expected alignment unit ID {expected_unit_id}, found {unit.id}")
        if unit.id in unit_ids:
            issues.append(f"Duplicate alignment unit ID: {unit.id}")
        unit_ids.append(unit.id)
        for language, side in ((Language.ZH, unit.zh), (Language.EN, unit.en), (Language.RU, unit.ru)):
            indices: list[int] = []
            for paragraph_id in _side_items(side):
                key = str(paragraph_id)
                used[key] += 1
                paragraph = paragraph_by_id.get(key)
                if paragraph is None:
                    issues.append(f"Invalid paragraph reference: {key}")
                    continue
                if paragraph.language != language:
                    issues.append(f"Paragraph {key} is on the wrong language side")
                indices.append(paragraph.index)
            if indices:
                if indices != sorted(indices) or len(indices) != len(set(indices)):
                    issues.append(f"Non-monotonic paragraph order inside unit {unit.id} ({language.value})")
                if indices[0] <= last_index[language]:
                    issues.append(f"Non-monotonic alignment at unit {unit.id} ({language.value})")
                last_index[language] = indices[-1]

    for paragraph_id, count in used.items():
        if count > 1:
            issues.append(f"Paragraph used {count} times: {paragraph_id}")
    for paragraph_id in sorted(expected_story - set(used)):
        issues.append(f"Missing story paragraph: {paragraph_id}")

    expected_after = unit_ids[:-1]
    actual_after = [boundary.after for boundary in gold.boundaries]
    if len(actual_after) != len(set(actual_after)):
        issues.append("Duplicate semantic boundary annotation")
    missing_boundaries = [unit_id for unit_id in expected_after if unit_id not in actual_after]
    extra_boundaries = [unit_id for unit_id in actual_after if unit_id not in expected_after]
    for unit_id in missing_boundaries:
        issues.append(f"Missing JOIN/BREAK boundary after {unit_id}")
    for unit_id in extra_boundaries:
        issues.append(f"Unexpected boundary after {unit_id}")
    return tuple(issues)


def validate_gold_chapter(gold: GoldChapter, chapters: tuple[Chapter, ...]) -> None:
    issues = collect_gold_issues(gold, chapters)
    if issues:
        raise GoldValidationError(issues)
