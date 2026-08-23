"""Non-authoritative trilingual projection retained for bootstrap/test wiring only.

The Phase 3 evaluator does not accept this projection. Independent Pairwise Gold
is the only pairwise benchmark truth.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

from ..domain import Chapter, Language
from ..gold.io import load_gold
from ..gold.model import GoldAlignmentSide, GoldChapter, GoldSourceRef
from ..gold.validation import validate_gold_chapter
from ..infrastructure.corpus_io import load_chapter
from ..infrastructure.paths import PathPolicy
from .model import AlignmentDirection


PairwiseSignature = tuple[tuple[str, ...] | None, tuple[str, ...] | None]


@dataclass(frozen=True)
class ProjectedGoldUnit:
    signature: PairwiseSignature


@dataclass(frozen=True)
class GoldPairwiseProjection:
    direction: AlignmentDirection
    gold: GoldChapter
    left_source: GoldSourceRef
    right_source: GoldSourceRef
    left_chapter: Chapter
    right_chapter: Chapter
    units: tuple[ProjectedGoldUnit, ...]
    left_disposition_ids: tuple[str, ...]
    right_disposition_ids: tuple[str, ...]
    out_of_pair_scope_gold_unit_count: int


def _side(unit: object, language: Language) -> GoldAlignmentSide:
    return getattr(unit, language.value)


def _signature_side(side: GoldAlignmentSide) -> tuple[str, ...] | None:
    return None if side.gap else tuple(str(item) for item in side.paragraphs)


def _single_by_language(values: tuple[object, ...], language: Language, context: str) -> object:
    matches = [item for item in values if getattr(item, "language") is language]
    if len(matches) != 1:
        raise ValueError(f"{context} requires exactly one {language.value} source, found {len(matches)}")
    return matches[0]


def project_validated_gold(
    gold: GoldChapter, chapters: tuple[Chapter, ...], direction: AlignmentDirection,
) -> GoldPairwiseProjection:
    """Build read-only bootstrap wiring; never treat the result as pairwise truth."""
    if direction not in (AlignmentDirection.ZH_EN, AlignmentDirection.ZH_RU):
        raise ValueError(f"Unsupported Gold projection direction: {direction}")
    validate_gold_chapter(gold, chapters)
    left_language, right_language = Language.ZH, direction.right_language
    left_source = _single_by_language(gold.sources, left_language, "Gold projection")
    right_source = _single_by_language(gold.sources, right_language, "Gold projection")
    left_chapter = _single_by_language(chapters, left_language, "Gold projection")
    right_chapter = _single_by_language(chapters, right_language, "Gold projection")
    assert isinstance(left_source, GoldSourceRef) and isinstance(right_source, GoldSourceRef)
    assert isinstance(left_chapter, Chapter) and isinstance(right_chapter, Chapter)

    units: list[ProjectedGoldUnit] = []
    out_of_scope = 0
    for unit in gold.alignment_units:
        left = _side(unit, left_language)
        right = _side(unit, right_language)
        if left.gap and right.gap:
            out_of_scope += 1
            continue
        units.append(ProjectedGoldUnit((_signature_side(left), _signature_side(right))))

    disposition_ids = {str(item.paragraph_id) for item in gold.paragraph_dispositions}
    left_dispositions = tuple(
        str(item.id) for item in left_chapter.paragraphs if str(item.id) in disposition_ids
    )
    right_dispositions = tuple(
        str(item.id) for item in right_chapter.paragraphs if str(item.id) in disposition_ids
    )
    return GoldPairwiseProjection(
        direction, gold, left_source, right_source, left_chapter, right_chapter,
        tuple(units), left_dispositions, right_dispositions, out_of_scope,
    )


def load_fully_validated_gold(
    gold_path: Path, root: Path,
) -> tuple[GoldChapter, tuple[Chapter, ...]]:
    """Load every declared normalized source, hash-check it, and fully validate Gold."""
    gold = load_gold(gold_path)
    paths = PathPolicy(root)
    chapters: list[Chapter] = []
    for source in gold.sources:
        path = paths.resolve(source.normalized_path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != source.normalized_sha256:
            raise ValueError(f"Gold normalized source checksum mismatch: {source.normalized_path}")
        chapter = load_chapter(path)
        if chapter.source != source.source_id:
            raise ValueError(f"Gold normalized source identity mismatch: {source.normalized_path}")
        chapters.append(chapter)
    result = tuple(chapters)
    validate_gold_chapter(gold, result)
    return gold, result
