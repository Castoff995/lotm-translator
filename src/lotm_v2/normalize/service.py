"""Conservative normalization that preserves raw text and exact source spans."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

from .. import NORMALIZED_SCHEMA_VERSION
from ..domain import (
    Chapter, ChapterId, Paragraph, ParagraphId, ParagraphizationMode,
    ParagraphType, Provenance, SourceManifest,
)
from ..infrastructure.paths import PathPolicy
from .paragraphization import (
    ParagraphSpan, ParagraphizationArtifact, SpanDisposition,
    artifact_file_sha256, load_artifact, validate_artifact,
)


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


def _blank_line_spans(lines: list[str]) -> tuple[ParagraphSpan, ...]:
    spans: list[ParagraphSpan] = []
    start: int | None = None
    for line_number, line in enumerate(lines, start=1):
        if line.strip() and start is None:
            start = line_number
        elif not line.strip() and start is not None:
            spans.append(ParagraphSpan(start, line_number - 1))
            start = None
    if start is not None:
        spans.append(ParagraphSpan(start, len(lines)))
    return tuple(spans)


def _one_per_line_spans(lines: list[str]) -> tuple[ParagraphSpan, ...]:
    return tuple(ParagraphSpan(index, index) for index, line in enumerate(lines, start=1) if line.strip())


def normalize_text_chapter(
    raw_text: str,
    manifest: SourceManifest,
    manifest_path: Path,
    chapter_number: int,
    raw_location: str,
    raw_sha256: str,
    artifact: ParagraphizationArtifact | None = None,
    artifact_path: str | None = None,
    artifact_sha256: str | None = None,
) -> Chapter:
    canonical = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    lines = canonical.splitlines()
    mode = manifest.descriptor.paragraphization_mode
    if mode is ParagraphizationMode.BLANK_LINES:
        spans = _blank_line_spans(lines)
    elif mode is ParagraphizationMode.ONE_PER_LINE:
        spans = _one_per_line_spans(lines)
    elif mode is ParagraphizationMode.MANUAL_SPANS:
        if artifact is None or artifact_path is None or artifact_sha256 is None:
            raise ValueError("manual_spans normalization requires a registered paragraphization artifact")
        validate_artifact(artifact, raw_text, manifest, chapter_number, raw_sha256)
        spans = tuple(span for span in artifact.spans if span.disposition is SpanDisposition.PARAGRAPH)
    else:  # pragma: no cover
        raise ValueError(f"Unsupported paragraphization mode: {mode}")

    chapter_id = ChapterId(manifest.descriptor.work_id, chapter_number)
    paragraphs: list[Paragraph] = []
    for index, span in enumerate(spans, start=1):
        raw_fragment = "\n".join(lines[span.start_line - 1:span.end_line])
        if not raw_fragment.strip():
            raise ValueError(f"Paragraph span {span.start_line}-{span.end_line} contains no text")
        paragraph_type, metadata = _classify(raw_fragment)
        metadata["raw_line_count"] = span.end_line - span.start_line + 1
        paragraphs.append(Paragraph(
            id=ParagraphId(manifest.descriptor.work_id, manifest.descriptor.source_id, chapter_number, index),
            source=manifest.descriptor.source_id, language=manifest.descriptor.language,
            chapter=chapter_id, index=index, raw_text=raw_fragment,
            normalized_text=_normalize_fragment(raw_fragment), paragraph_type=paragraph_type,
            metadata=metadata,
            provenance=Provenance(
                source_manifest=str(manifest_path), raw_location=raw_location,
                raw_sha256=raw_sha256, start_line=span.start_line, end_line=span.end_line,
                raw_paragraph_index=index,
                normalization_version=manifest.descriptor.normalization_version,
                paragraphization_artifact=artifact_path,
                paragraphization_sha256=artifact_sha256,
                paragraphization_version=(artifact.artifact_version if artifact else None),
            ),
        ))
    chapter_metadata: dict[str, str | int | float | bool | None] = {
        "normalization_version": manifest.descriptor.normalization_version,
        "paragraphization_mode": mode.value,
    }
    if artifact:
        chapter_metadata.update({
            "paragraphization_artifact": artifact_path,
            "paragraphization_sha256": artifact_sha256,
            "paragraphization_version": artifact.artifact_version,
        })
    return Chapter(
        schema_version=NORMALIZED_SCHEMA_VERSION, id=chapter_id,
        source=manifest.descriptor.source_id, language=manifest.descriptor.language,
        paragraphs=tuple(paragraphs), metadata=chapter_metadata,
    )


def normalize_manifest_chapter(manifest: SourceManifest, manifest_path: Path, chapter_number: int, paths: PathPolicy) -> Chapter:
    if manifest.descriptor.paragraphization_mode is ParagraphizationMode.EPUB_STRUCTURE:
        # Lazy import keeps EbookLib/lxml optional for text/OCR workflows.
        from .epub import normalize_epub_manifest_chapter
        return normalize_epub_manifest_chapter(manifest, manifest_path, chapter_number, paths)
    source = manifest.chapter(chapter_number)
    raw_path = paths.resolve(source.raw_location)
    content = raw_path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != source.sha256:
        raise ValueError(f"Raw checksum mismatch; immutable source changed: {raw_path}")
    artifact = None
    artifact_path = source.paragraphization_artifact
    artifact_hash = source.paragraphization_sha256
    if manifest.descriptor.paragraphization_mode is ParagraphizationMode.MANUAL_SPANS:
        if not artifact_path or not artifact_hash or not source.paragraphization_version:
            raise ValueError("manual_spans source has no confirmed registered paragraphization artifact")
        resolved_artifact = paths.resolve(artifact_path)
        actual_hash = artifact_file_sha256(resolved_artifact)
        if actual_hash != artifact_hash:
            raise ValueError("Registered paragraphization artifact changed; normalized corpus provenance is invalid")
        artifact = load_artifact(resolved_artifact)
        if artifact.artifact_version != source.paragraphization_version:
            raise ValueError("Paragraphization artifact version differs from source manifest")
    return normalize_text_chapter(
        content.decode("utf-8"), manifest, manifest_path, chapter_number,
        source.raw_location, source.sha256, artifact, artifact_path, artifact_hash,
    )
