"""Conservative normalization that preserves every raw paragraph."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

from .. import NORMALIZED_SCHEMA_VERSION
from ..domain import (
    Chapter, ChapterId, Paragraph, ParagraphId, ParagraphType, Provenance,
    SourceManifest,
)
from ..infrastructure.paths import PathPolicy


_BLANK_LINES = re.compile(r"\n[ \t]*\n+")
_SEPARATOR = re.compile(r"^(?:\*{3,}|_{3,}|-{3,})$")
_HEADING = re.compile(r"^(?:#\s*)?(?:chapter\s+\d+|第[〇一二三四五六七八九十百\d]+章|глава\s+\d+)\b", re.IGNORECASE)
_TRANSLATOR_NOTE = re.compile(r"(?:translator'?s note|прим\.\s*пер\.|примечани[ея]\s+переводчика)", re.IGNORECASE)
_FOOTNOTE = re.compile(r"^(?:\[\d+\]|\d+[.)]\s)")
_METADATA = re.compile(r"^(?:https?://|поддержать автора\b|comments?\s*:)", re.IGNORECASE)


def _classify(text: str) -> tuple[ParagraphType, dict[str, str | int | bool | None]]:
    stripped = text.strip()
    metadata: dict[str, str | int | bool | None] = {}
    if _SEPARATOR.fullmatch(stripped):
        return ParagraphType.SEPARATOR, metadata
    if _HEADING.match(stripped):
        return ParagraphType.HEADING, metadata
    if _TRANSLATOR_NOTE.search(stripped):
        metadata["review_flag"] = "translator_note_candidate"
        return ParagraphType.TRANSLATOR_NOTE, metadata
    if _FOOTNOTE.match(stripped):
        metadata["review_flag"] = "footnote_candidate"
        return ParagraphType.FOOTNOTE, metadata
    if _METADATA.match(stripped):
        return ParagraphType.METADATA, metadata
    if stripped.startswith(("—", "- ", "“", "\"", "「", "『", "«")):
        return ParagraphType.DIALOGUE, metadata
    return ParagraphType.STORY, metadata


def _normalize_fragment(raw_text: str) -> str:
    normalized = unicodedata.normalize("NFC", raw_text.replace("\r\n", "\n").replace("\r", "\n"))
    return " ".join(line.strip() for line in normalized.splitlines() if line.strip())


def normalize_text_chapter(raw_text: str, manifest: SourceManifest, manifest_path: Path, chapter_number: int, raw_location: str, raw_sha256: str) -> Chapter:
    canonical = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    raw_fragments = [fragment.strip() for fragment in _BLANK_LINES.split(canonical) if fragment.strip()]
    chapter_id = ChapterId(manifest.descriptor.work_id, chapter_number)
    paragraphs: list[Paragraph] = []
    for index, raw_fragment in enumerate(raw_fragments, start=1):
        paragraph_type, metadata = _classify(raw_fragment)
        metadata["raw_line_count"] = len(raw_fragment.splitlines())
        paragraphs.append(Paragraph(
            id=ParagraphId(manifest.descriptor.work_id, manifest.descriptor.source_id, chapter_number, index),
            source=manifest.descriptor.source_id, language=manifest.descriptor.language,
            chapter=chapter_id, index=index, raw_text=raw_fragment,
            normalized_text=_normalize_fragment(raw_fragment), paragraph_type=paragraph_type,
            metadata=metadata,
            provenance=Provenance(
                source_manifest=str(manifest_path), raw_location=raw_location,
                raw_sha256=raw_sha256, raw_paragraph_index=index,
                normalization_version=manifest.descriptor.normalization_version,
            ),
        ))
    return Chapter(
        schema_version=NORMALIZED_SCHEMA_VERSION, id=chapter_id,
        source=manifest.descriptor.source_id, language=manifest.descriptor.language,
        paragraphs=tuple(paragraphs),
        metadata={"normalization_version": manifest.descriptor.normalization_version},
    )


def normalize_manifest_chapter(manifest: SourceManifest, manifest_path: Path, chapter_number: int, paths: PathPolicy) -> Chapter:
    source = manifest.chapter(chapter_number)
    raw_path = paths.resolve(source.raw_location)
    content = raw_path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != source.sha256:
        raise ValueError(f"Raw checksum mismatch; immutable source changed: {raw_path}")
    return normalize_text_chapter(
        content.decode("utf-8"), manifest, manifest_path, chapter_number,
        source.raw_location, source.sha256,
    )

