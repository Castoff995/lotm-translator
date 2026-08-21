"""Typed Phase 2 gold corpus schema."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from ..domain import ChapterId, Language, ParagraphId, SourceId


class GoldStatus(str, Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"


class GoldFlag(str, Enum):
    ADDITION = "addition"
    OMISSION = "omission"
    TRANSLATOR_NOTE = "translator_note"
    EDITION_DIFFERENCE = "edition_difference"
    SCENE_BREAK = "scene_break"
    UNCERTAIN = "uncertain"


class BoundaryDecision(str, Enum):
    JOIN = "JOIN"
    BREAK = "BREAK"


@dataclass(frozen=True)
class GoldGap:
    reason: GoldFlag
    note: str | None = None

    def __post_init__(self) -> None:
        allowed = {GoldFlag.ADDITION, GoldFlag.OMISSION, GoldFlag.TRANSLATOR_NOTE, GoldFlag.EDITION_DIFFERENCE}
        if self.reason not in allowed:
            raise ValueError(f"{self.reason.value} is not a valid GAP reason")


@dataclass(frozen=True)
class GoldAlignmentSide:
    paragraphs: tuple[ParagraphId, ...] = ()
    gap: GoldGap | None = None

    def __post_init__(self) -> None:
        if bool(self.paragraphs) == bool(self.gap):
            raise ValueError("Alignment side must contain paragraph IDs or one explicit GAP")


@dataclass(frozen=True)
class GoldAlignmentUnit:
    id: str
    zh: GoldAlignmentSide
    en: GoldAlignmentSide
    ru: GoldAlignmentSide
    flags: tuple[GoldFlag, ...] = ()
    note: str | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9-]+:\d{4}:a\d{6}", self.id):
            raise ValueError(f"Invalid alignment unit id: {self.id!r}")
        if self.zh.gap and self.en.gap and self.ru.gap:
            raise ValueError("An alignment unit cannot contain GAP on every side")


@dataclass(frozen=True)
class GoldBoundary:
    after: str
    decision: BoundaryDecision
    note: str | None = None


@dataclass(frozen=True)
class GoldSourceRef:
    source_id: SourceId
    language: Language
    normalized_path: str
    normalized_sha256: str

    def __post_init__(self) -> None:
        if not self.normalized_path or not re.fullmatch(r"[0-9a-f]{64}", self.normalized_sha256):
            raise ValueError("Gold source reference requires path and SHA-256")


@dataclass(frozen=True)
class GoldChapter:
    schema_version: str
    status: GoldStatus
    chapter: ChapterId
    sources: tuple[GoldSourceRef, ...]
    alignment_units: tuple[GoldAlignmentUnit, ...] = ()
    boundaries: tuple[GoldBoundary, ...] = ()
    notes: str | None = None

    def __post_init__(self) -> None:
        source_ids = [str(source.source_id) for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("Gold chapter source IDs must be unique")


def alignment_unit_id(chapter: ChapterId, index: int) -> str:
    if index < 1:
        raise ValueError("Alignment unit index must be positive")
    return f"{chapter.work_id}:{chapter.number:04d}:a{index:06d}"

