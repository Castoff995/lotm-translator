"""Typed v2 world-glossary model."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


GLOSSARY_SCHEMA_VERSION = "1.0-draft"


class GlossaryStatus(str, Enum):
    NEEDS_RU = "needs_ru"
    COMPLETE = "complete"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True)
class GlossaryAliases:
    zh: tuple[str, ...] = ()
    en: tuple[str, ...] = ()
    ru: tuple[str, ...] = ()


@dataclass(frozen=True)
class GlossarySourceRef:
    work_id: str
    chapter: int
    paragraph_id: str
    start_offset: int
    end_offset: int

    def __post_init__(self) -> None:
        if not self.work_id or self.chapter < 1 or not self.paragraph_id:
            raise ValueError("Glossary source reference requires work/chapter/paragraph")
        if self.start_offset < 0 or self.end_offset <= self.start_offset:
            raise ValueError("Glossary source offsets must be a non-empty half-open span")


@dataclass(frozen=True)
class GlossaryProvenance:
    at: str
    actor: str
    tool_version: str


@dataclass(frozen=True)
class GlossaryEntry:
    id: str
    zh_term: str
    en_term: str
    ru_term: str | None
    status: GlossaryStatus
    aliases: GlossaryAliases
    source_refs: tuple[GlossarySourceRef, ...]
    notes: str | None
    created: GlossaryProvenance
    updated: GlossaryProvenance

    def __post_init__(self) -> None:
        if not self.id or not self.zh_term.strip() or not self.en_term.strip():
            raise ValueError("Glossary entry requires ID, Chinese term and English term")
        if self.status == GlossaryStatus.COMPLETE and not (self.ru_term and self.ru_term.strip()):
            raise ValueError("Complete glossary entry requires a Russian term")
        if not self.source_refs:
            raise ValueError("Glossary entry requires at least one source reference")


@dataclass(frozen=True)
class Glossary:
    schema_version: str
    work_id: str
    entries: tuple[GlossaryEntry, ...] = ()

    def __post_init__(self) -> None:
        if not self.work_id:
            raise ValueError("Glossary requires work_id")
        ids = [entry.id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("Glossary entry IDs must be unique")
