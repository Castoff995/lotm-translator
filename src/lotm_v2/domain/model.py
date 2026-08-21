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


class ParagraphizationMode(str, Enum):
    BLANK_LINES = "blank_lines"
    ONE_PER_LINE = "one_per_line"
    MANUAL_SPANS = "manual_spans"
    EPUB_STRUCTURE = "epub_structure"


class ProvenanceLocationKind(str, Enum):
    LINE_SPAN = "line_span"
    EPUB_DOM = "epub_dom"


@dataclass(frozen=True)
class EpubDomFragment:
    document_href: str
    spine_index: int
    dom_path: str
    element_tag: str
    document_sha256: str
    fragment_sha256: str
    element_id: str | None = None
    start_offset: int | None = None
    end_offset: int | None = None

    def __post_init__(self) -> None:
        if not self.document_href or self.spine_index < 0 or not self.dom_path.startswith("/") or not self.element_tag:
            raise ValueError("Invalid EPUB DOM fragment locator")
        for name, value in (("document", self.document_sha256), ("fragment", self.fragment_sha256)):
            if not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError(f"Invalid EPUB {name} checksum")
        if (self.start_offset is None) != (self.end_offset is None):
            raise ValueError("EPUB text offsets must be set together")
        if self.start_offset is not None and (self.start_offset < 0 or self.end_offset <= self.start_offset):
            raise ValueError("EPUB text offsets must form a non-empty half-open span")


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
    paragraphization_mode: ParagraphizationMode = ParagraphizationMode.BLANK_LINES

    def __post_init__(self) -> None:
        if not _ID_PART.fullmatch(self.work_id):
            raise ValueError(f"Invalid work id: {self.work_id!r}")
        if not self.edition.strip() or not self.normalization_version.strip():
            raise ValueError("Edition and normalization version are required")
        if self.paragraphization_mode is ParagraphizationMode.EPUB_STRUCTURE and self.source_format is not SourceFormat.EPUB:
            raise ValueError("epub_structure paragraphization requires format=epub")


@dataclass(frozen=True)
class SourceChapter:
    number: int
    raw_location: str
    sha256: str
    paragraphization_artifact: str | None = None
    paragraphization_sha256: str | None = None
    paragraphization_version: str | None = None

    def __post_init__(self) -> None:
        if self.number < 1 or not self.raw_location or not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("Invalid source chapter entry")
        artifact_fields = (self.paragraphization_artifact, self.paragraphization_sha256, self.paragraphization_version)
        if any(artifact_fields) and not all(artifact_fields):
            raise ValueError("Paragraphization artifact path, hash and version must be set together")
        if self.paragraphization_sha256 and not re.fullmatch(r"[0-9a-f]{64}", self.paragraphization_sha256):
            raise ValueError("Invalid paragraphization artifact checksum")


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

    def with_paragraphization(self, number: int, artifact_path: str, artifact_sha256: str, artifact_version: str) -> "SourceManifest":
        current = self.chapter(number)
        if current.paragraphization_sha256:
            existing = (current.paragraphization_artifact, current.paragraphization_sha256, current.paragraphization_version)
            requested = (artifact_path, artifact_sha256, artifact_version)
            if existing != requested:
                raise ValueError(
                    "Confirmed paragraphization cannot be replaced for the same source/chapter; "
                    "create an explicit new source_id or source revision"
                )
            return self
        updated = replace(
            current, paragraphization_artifact=artifact_path,
            paragraphization_sha256=artifact_sha256, paragraphization_version=artifact_version,
        )
        return replace(self, chapters=tuple(updated if item.number == number else item for item in self.chapters))


@dataclass(frozen=True)
class Provenance:
    source_manifest: str
    raw_location: str
    raw_sha256: str
    normalization_version: str
    location_kind: ProvenanceLocationKind = ProvenanceLocationKind.LINE_SPAN
    start_line: int | None = None
    end_line: int | None = None
    epub_fragments: tuple[EpubDomFragment, ...] = ()
    raw_paragraph_index: int | None = None
    paragraphization_artifact: str | None = None
    paragraphization_sha256: str | None = None
    paragraphization_version: str | None = None

    def __post_init__(self) -> None:
        if self.location_kind is ProvenanceLocationKind.LINE_SPAN:
            if self.start_line is None or self.end_line is None or self.start_line < 1 or self.end_line < self.start_line:
                raise ValueError("Provenance line span must be 1-based and ordered")
            if self.epub_fragments:
                raise ValueError("Line-span provenance cannot contain EPUB DOM fragments")
        elif self.location_kind is ProvenanceLocationKind.EPUB_DOM:
            if self.start_line is not None or self.end_line is not None or not self.epub_fragments:
                raise ValueError("EPUB provenance requires DOM fragments and no line numbers")
        if self.raw_paragraph_index is not None and self.raw_paragraph_index < 1:
            raise ValueError("Raw paragraph index must be positive")
        if not re.fullmatch(r"[0-9a-f]{64}", self.raw_sha256):
            raise ValueError("Invalid provenance checksum")
        artifact_fields = (self.paragraphization_artifact, self.paragraphization_sha256, self.paragraphization_version)
        if any(artifact_fields) and not all(artifact_fields):
            raise ValueError("Paragraphization provenance must include path, hash and version")
        if self.paragraphization_sha256 and not re.fullmatch(r"[0-9a-f]{64}", self.paragraphization_sha256):
            raise ValueError("Invalid paragraphization provenance checksum")


@dataclass(frozen=True)
class SourceSpan:
    start_line: int
    end_line: int

    def __post_init__(self) -> None:
        if self.start_line < 1 or self.end_line < self.start_line:
            raise ValueError("Source span must be 1-based and ordered")


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
