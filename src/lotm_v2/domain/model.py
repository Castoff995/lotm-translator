"""Core corpus domain model; deliberately contains no alignment algorithms."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TypeAlias


MetadataValue: TypeAlias = str | int | float | bool | None
Metadata: TypeAlias = dict[str, MetadataValue]
_ID_PART = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class Language(str, Enum):
    ZH = "zh"
    EN = "en"
    RU = "ru"


class SourceRole(str, Enum):
    ORIGINAL = "original"
    OFFICIAL = "official"
    SECONDARY = "secondary"


class SourceFormat(str, Enum):
    EPUB = "epub"
    TEXT = "text"
    OCR = "ocr"


class ParagraphType(str, Enum):
    STORY = "story"
    DIALOGUE = "dialogue"
    HEADING = "heading"
    TRANSLATOR_NOTE = "translator_note"
    FOOTNOTE = "footnote"
    SEPARATOR = "separator"
    METADATA = "metadata"
    UNKNOWN = "unknown"


@dataclass(frozen=True, order=True)
class SourceId:
    value: str

    def __post_init__(self) -> None:
        if not _ID_PART.fullmatch(self.value):
            raise ValueError(f"Invalid source id: {self.value!r}")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, order=True)
class ChapterId:
    work_id: str
    number: int

    def __post_init__(self) -> None:
        if not _ID_PART.fullmatch(self.work_id):
            raise ValueError(f"Invalid work id: {self.work_id!r}")
        if self.number < 1:
            raise ValueError("Chapter number must be positive")

    def __str__(self) -> str:
        return f"{self.work_id}:{self.number:04d}"


@dataclass(frozen=True, order=True)
class ParagraphId:
    work_id: str
    source_id: SourceId
    chapter: int
    index: int

    def __post_init__(self) -> None:
        if not _ID_PART.fullmatch(self.work_id):
            raise ValueError(f"Invalid work id: {self.work_id!r}")
        if self.chapter < 1 or self.index < 1:
            raise ValueError("Paragraph chapter and index must be positive")

    def __str__(self) -> str:
        return f"{self.work_id}:{self.source_id}:{self.chapter:04d}:p{self.index:06d}"

    @classmethod
    def parse(cls, value: str) -> "ParagraphId":
        try:
            work_id, source, chapter, paragraph = value.split(":")
            if not paragraph.startswith("p"):
                raise ValueError
            return cls(work_id, SourceId(source), int(chapter), int(paragraph[1:]))
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid paragraph id: {value!r}") from error


@dataclass(frozen=True)
class SourceDescriptor:
    source_id: SourceId
    work_id: str
    language: Language
    role: SourceRole
    edition: str
    source_format: SourceFormat
    normalization_version: str

    def __post_init__(self) -> None:
        if not _ID_PART.fullmatch(self.work_id):
            raise ValueError(f"Invalid work id: {self.work_id!r}")
        if not self.edition.strip() or not self.normalization_version.strip():
            raise ValueError("Edition and normalization version are required")


@dataclass(frozen=True)
class SourceChapter:
    number: int
    raw_location: str
    sha256: str

    def __post_init__(self) -> None:
        if self.number < 1 or not self.raw_location or not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("Invalid source chapter entry")


@dataclass(frozen=True)
class SourceManifest:
    schema_version: str
    descriptor: SourceDescriptor
    chapters: tuple[SourceChapter, ...] = ()

    def chapter(self, number: int) -> SourceChapter:
        for item in self.chapters:
            if item.number == number:
                return item
        raise KeyError(f"Chapter {number} is absent from source {self.descriptor.source_id}")

    def with_chapter(self, chapter: SourceChapter) -> "SourceManifest":
        existing = {item.number: item for item in self.chapters}
        if chapter.number in existing and existing[chapter.number] != chapter:
            raise ValueError(f"Chapter {chapter.number} already has different immutable provenance")
        existing[chapter.number] = chapter
        return replace(self, chapters=tuple(existing[key] for key in sorted(existing)))


@dataclass(frozen=True)
class Provenance:
    source_manifest: str
    raw_location: str
    raw_sha256: str
    raw_paragraph_index: int
    normalization_version: str

    def __post_init__(self) -> None:
        if self.raw_paragraph_index < 1:
            raise ValueError("Raw paragraph index must be positive")
        if not re.fullmatch(r"[0-9a-f]{64}", self.raw_sha256):
            raise ValueError("Invalid provenance checksum")


@dataclass(frozen=True)
class Paragraph:
    id: ParagraphId
    source: SourceId
    language: Language
    chapter: ChapterId
    index: int
    raw_text: str
    normalized_text: str
    paragraph_type: ParagraphType
    metadata: Metadata = field(default_factory=dict)
    provenance: Provenance | None = None

    def __post_init__(self) -> None:
        if self.index < 1 or not self.raw_text:
            raise ValueError("Paragraph index and raw text are required")
        if self.id.source_id != self.source or self.id.chapter != self.chapter.number or self.id.index != self.index:
            raise ValueError("Paragraph ID does not match paragraph coordinates")
        if self.id.work_id != self.chapter.work_id:
            raise ValueError("Paragraph and chapter work IDs differ")


@dataclass(frozen=True)
class ParagraphRef:
    paragraph_id: ParagraphId

    def __str__(self) -> str:
        return str(self.paragraph_id)


@dataclass(frozen=True)
class Chapter:
    schema_version: str
    id: ChapterId
    source: SourceId
    language: Language
    paragraphs: tuple[Paragraph, ...]
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        expected = list(range(1, len(self.paragraphs) + 1))
        if [paragraph.index for paragraph in self.paragraphs] != expected:
            raise ValueError("Paragraph indices must be contiguous and 1-based")
        for paragraph in self.paragraphs:
            if paragraph.chapter != self.id or paragraph.source != self.source or paragraph.language != self.language:
                raise ValueError("Paragraph coordinates differ from chapter coordinates")

