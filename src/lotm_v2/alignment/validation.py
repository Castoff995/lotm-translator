"""Gold-independent structural validation for machine alignment proposals."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path

from ..domain import Chapter, Language, ParagraphId
from ..infrastructure.corpus_io import chapter_from_dict
from ..infrastructure.json_io import decode_json_object
from ..infrastructure.paths import PathPolicy
from .model import (
    AlignmentDirection, CoverageMode, PairwiseAlignmentProposal, ProposalSide,
    ProposalSourceSnapshot, parameters_sha256, proposal_unit_id,
)


@dataclass(frozen=True)
class ProposalValidationError(ValueError):
    issues: tuple[str, ...]

    def __str__(self) -> str:
        return "Pairwise proposal validation failed:\n- " + "\n- ".join(self.issues)


@dataclass(frozen=True)
class ValidatedProposal:
    proposal: PairwiseAlignmentProposal
    left_chapter: Chapter
    right_chapter: Chapter
    left_path: Path
    right_path: Path


def _validate_source_snapshot(
    proposal: PairwiseAlignmentProposal,
    snapshot: ProposalSourceSnapshot,
    chapter: Chapter,
    actual_sha256: str,
    expected_language: Language,
    side_name: str,
    issues: list[str],
) -> None:
    if snapshot.language is not expected_language:
        issues.append(f"{side_name} source language must be {expected_language.value}")
    if chapter.language is not expected_language:
        issues.append(f"Loaded {side_name} chapter language must be {expected_language.value}")
    if snapshot.source_id != chapter.source:
        issues.append(f"{side_name} source ID mismatch: snapshot={snapshot.source_id}, chapter={chapter.source}")
    if chapter.id.work_id != proposal.work_id:
        issues.append(f"Loaded {side_name} chapter has wrong work ID: {chapter.id.work_id}")
    if chapter.id.number != proposal.chapter:
        issues.append(f"Loaded {side_name} chapter has wrong chapter: {chapter.id.number}")
    if snapshot.normalized_sha256 != actual_sha256:
        issues.append(f"{side_name} normalized SHA-256 mismatch")
    if snapshot.normalized_schema_version != chapter.schema_version:
        issues.append(f"{side_name} normalized schema-version mismatch")
    if snapshot.paragraph_count != len(chapter.paragraphs):
        issues.append(f"{side_name} paragraph-count mismatch")


def _side_indices(
    side: ProposalSide,
    side_name: str,
    unit_id: str,
    proposal: PairwiseAlignmentProposal,
    chapter: Chapter,
    paragraph_indices: dict[str, int],
    used: Counter[str],
    issues: list[str],
) -> list[int]:
    if side.unmatched:
        return []
    keys = [str(item) for item in side.paragraphs]
    if len(keys) != len(set(keys)):
        issues.append(f"Duplicate Paragraph inside {unit_id} {side_name} side")
    indices: list[int] = []
    for paragraph_id, key in zip(side.paragraphs, keys):
        used[key] += 1
        if paragraph_id.work_id != proposal.work_id:
            issues.append(f"Paragraph from wrong work on {side_name} side: {key}")
        if paragraph_id.chapter != proposal.chapter:
            issues.append(f"Paragraph from wrong chapter on {side_name} side: {key}")
        if paragraph_id.source_id != chapter.source:
            issues.append(f"Paragraph from wrong source on {side_name} side: {key}")
        index = paragraph_indices.get(key)
        if index is None:
            issues.append(f"Unknown normalized Paragraph on {side_name} side: {key}")
        else:
            indices.append(index)
    if indices and (indices != sorted(indices) or len(indices) != len(set(indices))):
        issues.append(f"Non-monotonic Paragraph order inside {unit_id} {side_name} side")
    if indices and indices != list(range(indices[0], indices[-1] + 1)):
        issues.append(f"Non-contiguous Paragraph range inside {unit_id} {side_name} side")
    return indices


def validate_proposal(
    proposal: PairwiseAlignmentProposal,
    left_chapter: Chapter,
    right_chapter: Chapter,
    left_normalized_sha256: str,
    right_normalized_sha256: str,
) -> None:
    """Validate one ordered path without consulting Gold."""
    issues: list[str] = []
    if proposal.producer.parameters_sha256 != parameters_sha256(proposal.producer.parameters):
        issues.append("Producer parameters changed after provenance hash was established")
    if proposal.direction not in (AlignmentDirection.ZH_EN, AlignmentDirection.ZH_RU):
        issues.append(f"Unsupported alignment direction: {proposal.direction}")
    if proposal.left_source.language is not Language.ZH:
        issues.append("Canonical proposal left source must be ZH")
    if proposal.right_source.language is not proposal.direction.right_language:
        issues.append(
            f"Canonical {proposal.direction.value} proposal right source must be "
            f"{proposal.direction.right_language.value}"
        )
    if proposal.left_source.source_id == proposal.right_source.source_id:
        issues.append("Proposal left and right source IDs must differ")
    _validate_source_snapshot(
        proposal, proposal.left_source, left_chapter, left_normalized_sha256,
        Language.ZH, "left", issues,
    )
    _validate_source_snapshot(
        proposal, proposal.right_source, right_chapter, right_normalized_sha256,
        proposal.direction.right_language, "right", issues,
    )

    left_indices = {str(item.id): item.index for item in left_chapter.paragraphs}
    right_indices = {str(item.id): item.index for item in right_chapter.paragraphs}
    left_used: Counter[str] = Counter()
    right_used: Counter[str] = Counter()
    last = {"left": 0, "right": 0}
    seen_unit_ids: Counter[str] = Counter()
    for position, unit in enumerate(proposal.units, start=1):
        seen_unit_ids[unit.id] += 1
        expected_id = proposal_unit_id(position)
        if unit.id != expected_id:
            issues.append(f"Expected proposal unit ID {expected_id}, found {unit.id}")
        if unit.left.unmatched and unit.right.unmatched:
            issues.append(f"Both sides unmatched in proposal unit {unit.id}")
        for score in unit.scores:
            if not math.isfinite(score.value):
                issues.append(f"Non-finite score in proposal unit {unit.id}: {score.name}")
        for side_name, side, chapter, index_by_id, used in (
            ("left", unit.left, left_chapter, left_indices, left_used),
            ("right", unit.right, right_chapter, right_indices, right_used),
        ):
            indices = _side_indices(
                side, side_name, unit.id, proposal, chapter, index_by_id, used, issues,
            )
            if indices and indices[0] <= last[side_name]:
                issues.append(f"Non-monotonic/crossing {side_name} order at proposal unit {unit.id}")
            if indices:
                last[side_name] = indices[-1]
    for unit_id, count in seen_unit_ids.items():
        if count > 1:
            issues.append(f"Duplicate proposal unit ID: {unit_id}")
    for side_name, used in (("left", left_used), ("right", right_used)):
        for paragraph_id, count in used.items():
            if count > 1:
                issues.append(f"Paragraph used {count} times on {side_name} side: {paragraph_id}")

    if proposal.coverage_mode is CoverageMode.COMPLETE:
        for side_name, chapter, used in (
            ("left", left_chapter, left_used), ("right", right_chapter, right_used),
        ):
            expected = {str(item.id) for item in chapter.paragraphs}
            missing = sorted(expected - set(used))
            if missing:
                issues.append(
                    f"Complete proposal skips {len(missing)} {side_name} Paragraphs: {', '.join(missing)}"
                )
    if issues:
        raise ProposalValidationError(tuple(issues))


def _snapshot_path(root: Path, snapshot: ProposalSourceSnapshot, chapter: int) -> Path:
    return PathPolicy(root).require_normalized_chapter_path(
        snapshot.normalized_path, snapshot.source_id, chapter,
    )


def load_and_validate_proposal(
    proposal: PairwiseAlignmentProposal, root: Path,
) -> ValidatedProposal:
    left_path = _snapshot_path(root, proposal.left_source, proposal.chapter)
    right_path = _snapshot_path(root, proposal.right_source, proposal.chapter)
    left_bytes = left_path.read_bytes()
    right_bytes = right_path.read_bytes()
    left = chapter_from_dict(decode_json_object(left_bytes, str(left_path)))
    right = chapter_from_dict(decode_json_object(right_bytes, str(right_path)))
    validate_proposal(
        proposal, left, right, hashlib.sha256(left_bytes).hexdigest(), hashlib.sha256(right_bytes).hexdigest(),
    )
    return ValidatedProposal(proposal, left, right, left_path, right_path)


def validation_summary(validated: ValidatedProposal) -> dict[str, object]:
    proposal = validated.proposal
    covered_left = sum(len(unit.left.paragraphs) for unit in proposal.units)
    covered_right = sum(len(unit.right.paragraphs) for unit in proposal.units)
    return {
        "schema_version": proposal.schema_version,
        "artifact_type": proposal.artifact_type,
        "work_id": proposal.work_id,
        "chapter": proposal.chapter,
        "direction": proposal.direction.value,
        "coverage_mode": proposal.coverage_mode.value,
        "proposal_unit_count": len(proposal.units),
        "sources": {
            "left": {
                "source_id": str(proposal.left_source.source_id),
                "language": proposal.left_source.language.value,
                "paragraph_count": len(validated.left_chapter.paragraphs),
                "covered_paragraph_count": covered_left,
            },
            "right": {
                "source_id": str(proposal.right_source.source_id),
                "language": proposal.right_source.language.value,
                "paragraph_count": len(validated.right_chapter.paragraphs),
                "covered_paragraph_count": covered_right,
            },
        },
        "validation": {
            "proposal_valid": True,
            "source_identity_verified": True,
            "normalized_hashes_verified": True,
        },
    }
