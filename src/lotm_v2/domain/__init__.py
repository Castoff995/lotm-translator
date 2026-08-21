"""Typed domain entities shared by v2 foundation layers."""

from .model import (
    Chapter,
    ChapterId,
    EpubDomFragment,
    Language,
    Paragraph,
    ParagraphId,
    ParagraphRef,
    ParagraphizationMode,
    ParagraphType,
    Provenance,
    ProvenanceLocationKind,
    SourceSpan,
    SourceChapter,
    SourceDescriptor,
    SourceFormat,
    SourceId,
    SourceManifest,
    SourceRole,
)

__all__ = [
    "Chapter", "ChapterId", "EpubDomFragment", "Language", "Paragraph", "ParagraphId",
    "ParagraphRef", "ParagraphizationMode", "ParagraphType", "Provenance", "ProvenanceLocationKind", "SourceSpan", "SourceChapter",
    "SourceDescriptor", "SourceFormat", "SourceId", "SourceManifest", "SourceRole",
]
