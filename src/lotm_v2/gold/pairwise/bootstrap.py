"""Read-only trilingual-Gold bootstrap assistance for Pairwise Review."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ...domain import Chapter, Language, SourceId
from ...infrastructure.corpus_io import load_chapter
from ...infrastructure.paths import PathPolicy
from ..io import gold_document_sha256, load_gold
from ..model import GoldAlignmentSide, GoldChapter, GoldSourceRef
from ..validation import validate_gold_chapter


def _safe_source_path(root: Path, stored: str) -> Path:
    root = root.resolve()
    candidate = Path(stored)
    path = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError(f"Trilingual bootstrap source escapes project root: {stored}") from error
    return path


def load_validated_trilingual_bootstrap(
    gold_path: Path, root: Path,
) -> tuple[GoldChapter, tuple[Chapter, ...]]:
    gold = load_gold(gold_path)
    chapters: list[Chapter] = []
    for source in gold.sources:
        path = _safe_source_path(root, source.normalized_path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != source.normalized_sha256:
            raise ValueError(f"Trilingual bootstrap source hash mismatch: {source.normalized_path}")
        chapter = load_chapter(path)
        if chapter.source != source.source_id:
            raise ValueError(f"Trilingual bootstrap source identity mismatch: {source.source_id}")
        chapters.append(chapter)
    result = tuple(chapters)
    validate_gold_chapter(gold, result)
    return gold, result


def _side(unit: object, language: Language) -> GoldAlignmentSide:
    return getattr(unit, language.value)


def _side_payload(side: GoldAlignmentSide) -> dict[str, Any]:
    if side.gap:
        return {"gap": {"reason": side.gap.reason.value, "note": side.gap.note}}
    return {"paragraphs": [str(item) for item in side.paragraphs]}


def _exact_source(
    gold: GoldChapter, chapters: tuple[Chapter, ...], source_id: SourceId,
) -> tuple[GoldSourceRef, Chapter]:
    refs = [item for item in gold.sources if item.source_id == source_id]
    values = [item for item in chapters if item.source == source_id]
    if len(refs) != 1 or len(values) != 1:
        raise ValueError(f"Bootstrap requires exact declared source ID {source_id}")
    return refs[0], values[0]


def inspect_trilingual_bootstrap(
    gold_path: Path, root: Path, left_source_id: str, right_source_id: str,
) -> dict[str, Any]:
    """Return deterministic suggestions without creating or mutating Pairwise Gold."""
    gold, chapters = load_validated_trilingual_bootstrap(gold_path, root)
    left_ref, left_chapter = _exact_source(gold, chapters, SourceId(left_source_id))
    right_ref, right_chapter = _exact_source(gold, chapters, SourceId(right_source_id))
    if left_chapter.language is not Language.ZH or right_chapter.language not in (Language.EN, Language.RU):
        raise ValueError("Bootstrap supports exact zh+en or zh+ru source pairs only")
    if left_ref.source_id == right_ref.source_id:
        raise ValueError("Bootstrap source IDs must differ")
    third = [item for item in chapters if item.source not in {left_ref.source_id, right_ref.source_id}]
    if len(third) != 1:
        raise ValueError("Trilingual bootstrap requires exactly one third-language source")
    third_chapter = third[0]
    digest = gold_document_sha256(gold)
    items: list[dict[str, Any]] = []
    for unit in gold.alignment_units:
        left_side = _side(unit, left_chapter.language)
        right_side = _side(unit, right_chapter.language)
        third_side = _side(unit, third_chapter.language)
        both_gap = bool(left_side.gap and right_side.gap)
        kind = (
            "out_of_pair_scope" if both_gap else
            "gap_suggestion" if bool(left_side.gap) != bool(right_side.gap) else
            "coarse_region"
        )
        items.append({
            "kind": kind,
            "trilingual_unit_id": unit.id,
            "trilingual_gold_document_sha256": digest,
            "selected_source_ids": [left_source_id, right_source_id],
            "left": _side_payload(left_side),
            "right": _side_payload(right_side),
            "third_language_context": {
                "source_id": str(third_chapter.source),
                "language": third_chapter.language.value,
                **_side_payload(third_side),
            },
            "confirmed_pairwise_truth": False,
        })
    selected = {left_ref.source_id, right_ref.source_id}
    for disposition in gold.paragraph_dispositions:
        if disposition.paragraph_id.source_id not in selected:
            continue
        items.append({
            "kind": "disposition_suggestion",
            "trilingual_disposition_identity": str(disposition.paragraph_id),
            "trilingual_gold_document_sha256": digest,
            "selected_source_ids": [left_source_id, right_source_id],
            "paragraph_id": str(disposition.paragraph_id),
            "reason": disposition.reason.value,
            "note": disposition.note,
            "after_alignment_unit": disposition.after_alignment_unit,
            "third_language_context": {"source_id": str(third_chapter.source), "language": third_chapter.language.value},
            "confirmed_pairwise_truth": False,
        })
    return {
        "artifact_type": "trilingual_pairwise_bootstrap_inspection",
        "authoritative": False,
        "label": "TRILINGUAL BOOTSTRAP — NOT PAIRWISE TRUTH",
        "work_id": gold.chapter.work_id,
        "chapter": gold.chapter.number,
        "trilingual_gold_path": PathPolicy(root).relative(gold_path),
        "trilingual_gold_document_sha256": digest,
        "left_source_id": left_source_id,
        "right_source_id": right_source_id,
        "third_source_id": str(third_chapter.source),
        "items": items,
    }
