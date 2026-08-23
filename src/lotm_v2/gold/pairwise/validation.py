"""Full source-bound validation for independent Pairwise Gold."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
from pathlib import Path

from ...domain import Chapter, Language
from ...infrastructure.corpus_io import chapter_from_dict
from ...infrastructure.json_io import decode_json_object
from ...infrastructure.paths import PathPolicy
from .model import (
    PairwiseAlignmentSide, PairwiseDirection, PairwiseGold, PairwiseGoldSource,
    PairwiseGoldStatus, pairwise_alignment_unit_id,
)


@dataclass(frozen=True)
class PairwiseGoldValidationError(ValueError):
    issues: tuple[str, ...]

    def __str__(self) -> str:
        return "Pairwise Gold validation failed:\n- " + "\n- ".join(self.issues)


@dataclass(frozen=True)
class ValidatedPairwiseGold:
    gold: PairwiseGold
    left_chapter: Chapter
    right_chapter: Chapter
    left_path: Path
    right_path: Path


def _source_issues(
    gold: PairwiseGold, snapshot: PairwiseGoldSource, chapter: Chapter,
    actual_hash: str, expected_language: Language, side: str,
) -> list[str]:
    issues: list[str] = []
    if snapshot.language is not expected_language or chapter.language is not expected_language:
        issues.append(f"{side} language must be {expected_language.value}")
    if snapshot.source_id != chapter.source:
        issues.append(f"{side} exact source ID mismatch")
    if chapter.id.work_id != gold.work_id or chapter.id.number != gold.chapter:
        issues.append(f"{side} normalized chapter identity mismatch")
    if snapshot.normalized_sha256 != actual_hash:
        issues.append(f"{side} normalized SHA-256 mismatch")
    if snapshot.normalized_schema_version != chapter.schema_version:
        issues.append(f"{side} normalized schema version mismatch")
    if snapshot.paragraph_count != len(chapter.paragraphs):
        issues.append(f"{side} paragraph count mismatch")
    return issues


def _side_indices(
    gold: PairwiseGold, side: PairwiseAlignmentSide, chapter: Chapter,
    side_name: str, unit_id: str, used: Counter[str], issues: list[str],
) -> list[int]:
    if side.gap:
        return []
    lookup = {str(item.id): item.index for item in chapter.paragraphs}
    indices: list[int] = []
    for paragraph_id in side.paragraphs:
        key = str(paragraph_id)
        used[key] += 1
        if paragraph_id.work_id != gold.work_id or paragraph_id.chapter != gold.chapter:
            issues.append(f"Wrong work/chapter Paragraph on {side_name} side: {key}")
        if paragraph_id.source_id != chapter.source:
            issues.append(f"Wrong exact source Paragraph on {side_name} side: {key}")
        index = lookup.get(key)
        if index is None:
            issues.append(f"Unknown Paragraph on {side_name} side: {key}")
        else:
            indices.append(index)
    if indices != sorted(indices) or len(indices) != len(set(indices)):
        issues.append(f"Non-monotonic Paragraph order inside {unit_id} {side_name} side")
    if indices and indices != list(range(indices[0], indices[-1] + 1)):
        issues.append(f"Non-contiguous Paragraph range inside {unit_id} {side_name} side")
    return indices


def collect_pairwise_gold_issues(
    gold: PairwiseGold, left: Chapter, right: Chapter,
    left_sha256: str, right_sha256: str, *, require_complete: bool = True,
) -> tuple[str, ...]:
    issues: list[str] = []
    if gold.direction not in (PairwiseDirection.ZH_EN, PairwiseDirection.ZH_RU):
        issues.append(f"Unsupported Pairwise Gold direction: {gold.direction}")
    issues.extend(_source_issues(gold, gold.left_source, left, left_sha256, Language.ZH, "left"))
    issues.extend(_source_issues(
        gold, gold.right_source, right, right_sha256, gold.direction.right_language, "right",
    ))
    left_used: Counter[str] = Counter()
    right_used: Counter[str] = Counter()
    last = {"left": 0, "right": 0}
    seen_ids: Counter[str] = Counter()
    for position, unit in enumerate(gold.alignment_units, start=1):
        seen_ids[unit.id] += 1
        expected = pairwise_alignment_unit_id(
            gold.chapter_id, gold.left_source.source_id, gold.right_source.source_id, position,
        )
        if unit.id != expected:
            issues.append(f"Expected Pairwise AlignmentUnit ID {expected}, found {unit.id}")
        if unit.left.gap and unit.right.gap:
            issues.append(f"Both sides GAP in Pairwise AlignmentUnit {unit.id}")
        for side_name, side_value, chapter_value, used in (
            ("left", unit.left, left, left_used), ("right", unit.right, right, right_used),
        ):
            indices = _side_indices(gold, side_value, chapter_value, side_name, unit.id, used, issues)
            if indices and indices[0] <= last[side_name]:
                issues.append(f"Non-monotonic/crossing {side_name} order at {unit.id}")
            if indices:
                last[side_name] = indices[-1]
    for unit_id, count in seen_ids.items():
        if count > 1:
            issues.append(f"Duplicate Pairwise AlignmentUnit ID: {unit_id}")
    for side_name, used in (("left", left_used), ("right", right_used)):
        for paragraph_id, count in used.items():
            if count > 1:
                issues.append(f"Paragraph used {count} times in Pairwise units ({side_name}): {paragraph_id}")

    lookup = {str(item.id): item for chapter in (left, right) for item in chapter.paragraphs}
    dispositions: Counter[str] = Counter()
    last_anchor = -1
    for item in gold.paragraph_dispositions:
        key = str(item.paragraph_id)
        dispositions[key] += 1
        paragraph = lookup.get(key)
        if paragraph is None:
            issues.append(f"Invalid Pairwise disposition Paragraph reference: {key}")
        elif paragraph.source not in {left.source, right.source}:
            issues.append(f"Disposition Paragraph is outside the exact source pair: {key}")
        if item.after_alignment_unit > len(gold.alignment_units):
            issues.append(f"Pairwise disposition anchor exceeds unit range: {key}")
        if item.after_alignment_unit < last_anchor:
            issues.append("Pairwise dispositions must have monotonic unit anchors")
        last_anchor = item.after_alignment_unit
    for paragraph_id, count in dispositions.items():
        if count > 1:
            issues.append(f"Paragraph dispositioned {count} times: {paragraph_id}")
    aligned = set(left_used) | set(right_used)
    for paragraph_id in sorted(aligned & set(dispositions)):
        issues.append(f"Paragraph is both aligned and dispositioned: {paragraph_id}")

    dispositions_by_anchor: dict[int, list[object]] = {}
    for item in gold.paragraph_dispositions:
        dispositions_by_anchor.setdefault(item.after_alignment_unit, []).append(item)
    fate_cursor = {left.source: 0, right.source: 0}

    def consume_in_fate_order(paragraph_id: object, fate: str) -> None:
        paragraph = lookup.get(str(paragraph_id))
        if paragraph is None or paragraph.source not in fate_cursor:
            return
        expected_index = fate_cursor[paragraph.source] + 1
        if paragraph.index != expected_index:
            issues.append(
                f"Non-monotonic Pairwise Gold fate order for {paragraph.source}: "
                f"expected p{expected_index}, found p{paragraph.index} in {fate}"
            )
        fate_cursor[paragraph.source] = paragraph.index

    for anchor in range(len(gold.alignment_units) + 1):
        for disposition in dispositions_by_anchor.get(anchor, []):
            consume_in_fate_order(disposition.paragraph_id, "disposition")
        if anchor == len(gold.alignment_units):
            continue
        unit = gold.alignment_units[anchor]
        for side in (unit.left, unit.right):
            if side.gap:
                continue
            for paragraph_id in side.paragraphs:
                consume_in_fate_order(paragraph_id, f"unit {unit.id}")

    complete = require_complete or gold.status is PairwiseGoldStatus.CONFIRMED
    if complete:
        expected = {str(item.id) for chapter in (left, right) for item in chapter.paragraphs}
        covered = aligned | set(dispositions)
        for paragraph_id in sorted(expected - covered):
            issues.append(f"Missing Pairwise Gold fate for physical Paragraph: {paragraph_id}")
    return tuple(issues)


def validate_pairwise_gold(
    gold: PairwiseGold, left: Chapter, right: Chapter,
    left_sha256: str, right_sha256: str, *, require_complete: bool = True,
) -> None:
    issues = collect_pairwise_gold_issues(
        gold, left, right, left_sha256, right_sha256, require_complete=require_complete,
    )
    if issues:
        raise PairwiseGoldValidationError(issues)


def load_and_validate_pairwise_gold(
    gold: PairwiseGold, root: Path, *, require_complete: bool = True,
) -> ValidatedPairwiseGold:
    paths = PathPolicy(root)
    left_path = paths.require_normalized_chapter_path(
        gold.left_source.normalized_path, gold.left_source.source_id, gold.chapter,
    )
    right_path = paths.require_normalized_chapter_path(
        gold.right_source.normalized_path, gold.right_source.source_id, gold.chapter,
    )
    left_bytes, right_bytes = left_path.read_bytes(), right_path.read_bytes()
    left = chapter_from_dict(decode_json_object(left_bytes, str(left_path)))
    right = chapter_from_dict(decode_json_object(right_bytes, str(right_path)))
    validate_pairwise_gold(
        gold, left, right, hashlib.sha256(left_bytes).hexdigest(),
        hashlib.sha256(right_bytes).hexdigest(), require_complete=require_complete,
    )
    return ValidatedPairwiseGold(gold, left, right, left_path, right_path)


def pairwise_validation_summary(value: ValidatedPairwiseGold) -> dict[str, object]:
    gold = value.gold
    return {
        "schema_version": gold.schema_version, "artifact_type": gold.artifact_type,
        "status": gold.status.value, "work_id": gold.work_id, "chapter": gold.chapter,
        "direction": gold.direction.value, "pair_key": gold.pair_key,
        "alignment_unit_count": len(gold.alignment_units),
        "paragraph_disposition_count": len(gold.paragraph_dispositions),
        "physical_paragraph_count": len(value.left_chapter.paragraphs) + len(value.right_chapter.paragraphs),
        "validation": {"full_coverage": True, "source_identity_verified": True, "normalized_hashes_verified": True},
    }
