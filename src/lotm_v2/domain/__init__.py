"""Typed domain entities shared by v2 foundation layers."""

from .model import (
    Chapter,
    ChapterId,
    Language,
    Paragraph,
    ParagraphId,
    ParagraphRef,
    ParagraphizationMode,
    ParagraphType,
    Provenance,
    SourceSpan,
    SourceChapter,
    SourceDescriptor,
    SourceFormat,
    SourceId,
    SourceManifest,
    SourceRole,
)

__all__ = [
    "Chapter", "ChapterId", "Language", "Paragraph", "ParagraphId",
    "ParagraphRef", "ParagraphizationMode", "ParagraphType", "Provenance", "SourceSpan", "SourceChapter",
    "SourceDescriptor", "SourceFormat", "SourceId", "SourceManifest", "SourceRole",
]
