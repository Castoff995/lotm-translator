"""Typed persistent model for independent human Pairwise Gold."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from ...domain import ChapterId, Language, ParagraphId, SourceId
from ..model import GoldFlag, ParagraphDispositionReason


PAIRWISE_GOLD_SCHEMA_VERSION = "1.0-draft"
PAIRWISE_GOLD_ARTIFACT_TYPE = "pairwise_gold"


class PairwiseDirection(str, Enum):
    ZH_EN = "zh-en"
    ZH_RU = "zh-ru"

    @property
    def right_language(self) -> Language:
        return Language.EN if self is PairwiseDirection.ZH_EN else Language.RU


class PairwiseGoldStatus(str, Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"


class PairwiseCreationMode(str, Enum):
    EMPTY = "empty"
    TRILINGUAL_BOOTSTRAP = "trilingual_bootstrap"


@dataclass(frozen=True)
class PairwiseGoldSource:
    source_id: SourceId
    language: Language
    normalized_path: str
    normalized_sha256: str
    normalized_schema_version: str
    paragraph_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, SourceId) or not isinstance(self.language, Language):
            raise ValueError("Pairwise Gold source requires typed source/language identity")
        if not self.normalized_path or not self.normalized_schema_version:
            raise ValueError("Pairwise Gold source requires normalized path and schema version")
        if not re.fullmatch(r"[0-9a-f]{64}", self.normalized_sha256):
            raise ValueError("Pairwise Gold source requires normalized SHA-256")
        if isinstance(self.paragraph_count, bool) or not isinstance(self.paragraph_count, int) or self.paragraph_count < 0:
            raise ValueError("Pairwise Gold source paragraph count must be non-negative")


@dataclass(frozen=True)
class PairwiseGap:
    reason: GoldFlag
    note: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.reason, GoldFlag):
            raise ValueError("Pairwise Gold GAP requires a typed human reason")
        allowed = {
            GoldFlag.ADDITION, GoldFlag.OMISSION,
            GoldFlag.TRANSLATOR_NOTE, GoldFlag.EDITION_DIFFERENCE,
        }
        if self.reason not in allowed:
            raise ValueError(f"{self.reason.value} is not a valid Pairwise Gold GAP reason")


@dataclass(frozen=True)
class PairwiseAlignmentSide:
    paragraphs: tuple[ParagraphId, ...] = ()
    gap: PairwiseGap | None = None

    def __post_init__(self) -> None:
        if bool(self.paragraphs) == bool(self.gap):
            raise ValueError("Pairwise Gold side requires Paragraphs or one human GAP")


@dataclass(frozen=True)
class PairwiseAlignmentUnit:
    id: str
    left: PairwiseAlignmentSide
    right: PairwiseAlignmentSide
    note: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not isinstance(self.left, PairwiseAlignmentSide) or not isinstance(self.right, PairwiseAlignmentSide):
            raise ValueError("Pairwise AlignmentUnit requires ID and two typed sides")
        if self.left.gap and self.right.gap:
            raise ValueError("Pairwise AlignmentUnit cannot contain GAP on both sides")


@dataclass(frozen=True)
class PairwiseParagraphDisposition:
    paragraph_id: ParagraphId
    reason: ParagraphDispositionReason
    note: str | None = None
    after_alignment_unit: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.paragraph_id, ParagraphId) or not isinstance(self.reason, ParagraphDispositionReason):
            raise ValueError("Pairwise disposition requires typed Paragraph ID and reason")
        if isinstance(self.after_alignment_unit, bool) or not isinstance(self.after_alignment_unit, int) or self.after_alignment_unit < 0:
            raise ValueError("Pairwise disposition alignment anchor cannot be negative")
        if self.reason is ParagraphDispositionReason.OTHER and not (self.note and self.note.strip()):
            raise ValueError("Pairwise disposition reason 'other' requires a note")


@dataclass(frozen=True)
class PairwiseGoldProvenance:
    creation_mode: PairwiseCreationMode
    bootstrap_trilingual_gold_path: str | None = None
    bootstrap_trilingual_gold_document_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.creation_mode, PairwiseCreationMode):
            raise ValueError("Pairwise Gold provenance requires a typed creation mode")
        fields = (
            self.bootstrap_trilingual_gold_path,
            self.bootstrap_trilingual_gold_document_sha256,
        )
        if any(fields) != all(fields):
            raise ValueError("Pairwise bootstrap path and document SHA-256 must be set together")
        if self.bootstrap_trilingual_gold_document_sha256 and not re.fullmatch(
            r"[0-9a-f]{64}", self.bootstrap_trilingual_gold_document_sha256,
        ):
            raise ValueError("Invalid trilingual bootstrap document SHA-256")
        if self.creation_mode is PairwiseCreationMode.EMPTY and any(fields):
            raise ValueError("Empty Pairwise Gold provenance cannot claim a bootstrap")
        if self.creation_mode is PairwiseCreationMode.TRILINGUAL_BOOTSTRAP and not all(fields):
            raise ValueError("Trilingual-bootstrap creation mode requires bootstrap identity")


@dataclass(frozen=True)
class PairwiseGold:
    schema_version: str
    artifact_type: str
    status: PairwiseGoldStatus
    work_id: str
    chapter: int
    direction: PairwiseDirection
    left_source: PairwiseGoldSource
    right_source: PairwiseGoldSource
    alignment_units: tuple[PairwiseAlignmentUnit, ...] = ()
    paragraph_dispositions: tuple[PairwiseParagraphDisposition, ...] = ()
    provenance: PairwiseGoldProvenance = PairwiseGoldProvenance(PairwiseCreationMode.EMPTY)
    notes: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != PAIRWISE_GOLD_SCHEMA_VERSION:
            raise ValueError(f"Unsupported Pairwise Gold schema: {self.schema_version!r}")
        if self.artifact_type != PAIRWISE_GOLD_ARTIFACT_TYPE:
            raise ValueError(f"Unsupported Pairwise Gold artifact type: {self.artifact_type!r}")
        if not isinstance(self.status, PairwiseGoldStatus) or not isinstance(self.direction, PairwiseDirection):
            raise ValueError("Pairwise Gold requires typed status and direction")
        if not isinstance(self.left_source, PairwiseGoldSource) or not isinstance(self.right_source, PairwiseGoldSource):
            raise ValueError("Pairwise Gold requires two typed source snapshots")
        if not isinstance(self.work_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", self.work_id):
            raise ValueError("Invalid Pairwise Gold work identity")
        if isinstance(self.chapter, bool) or not isinstance(self.chapter, int) or self.chapter < 1:
            raise ValueError("Invalid Pairwise Gold work/chapter identity")
        if any(not isinstance(item, PairwiseAlignmentUnit) for item in self.alignment_units):
            raise ValueError("Pairwise Gold alignment_units must contain typed units")
        if any(not isinstance(item, PairwiseParagraphDisposition) for item in self.paragraph_dispositions):
            raise ValueError("Pairwise Gold dispositions must contain typed decisions")
        if self.left_source.source_id == self.right_source.source_id:
            raise ValueError("Pairwise Gold source IDs must differ")
        if self.left_source.language is not Language.ZH:
            raise ValueError("Canonical Pairwise Gold left language must be zh")
        if self.right_source.language is not self.direction.right_language:
            raise ValueError(
                f"Canonical {self.direction.value} Pairwise Gold right language must be "
                f"{self.direction.right_language.value}"
            )

    @property
    def chapter_id(self) -> ChapterId:
        return ChapterId(self.work_id, self.chapter)

    @property
    def pair_key(self) -> str:
        return f"{self.left_source.source_id}--{self.right_source.source_id}"


def pairwise_alignment_unit_id(
    chapter: ChapterId, left_source: SourceId, right_source: SourceId, index: int,
) -> str:
    if index < 1:
        raise ValueError("Pairwise AlignmentUnit index must be positive")
    return (
        f"{chapter.work_id}:{chapter.number:04d}:"
        f"{left_source}--{right_source}:a{index:06d}"
    )
