"""Versioned, human-reviewed paragraphization artifacts for line-based sources."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..domain import ParagraphizationMode, SourceId, SourceManifest, SourceSpan
from ..infrastructure.corpus_io import save_manifest
from ..infrastructure.json_io import read_json, write_json
from ..infrastructure.paths import PathPolicy


class SpanDisposition(str, Enum):
    PARAGRAPH = "paragraph"
    EXCLUDED = "excluded"


class ExclusionReason(str, Enum):
    HEADING = "heading"
    METADATA = "metadata"
    OCR_ARTIFACT = "ocr_artifact"
    OTHER = "other"


@dataclass(frozen=True)
class ParagraphSpan:
    start_line: int
    end_line: int
    disposition: SpanDisposition = SpanDisposition.PARAGRAPH
    reason: ExclusionReason | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        SourceSpan(self.start_line, self.end_line)
        if self.disposition is SpanDisposition.EXCLUDED and self.reason is None:
            raise ValueError("Excluded span requires an explicit reason")
        if self.disposition is SpanDisposition.PARAGRAPH and self.reason is not None:
            raise ValueError("Paragraph span cannot have an exclusion reason")
        if self.reason is ExclusionReason.OTHER and not (self.note and self.note.strip()):
            raise ValueError("Exclusion reason 'other' requires a note")


@dataclass(frozen=True)
class ParagraphizationArtifact:
    schema_version: str
    artifact_version: str
    work_id: str
    source_id: SourceId
    chapter: int
    mode: ParagraphizationMode
    raw_sha256: str
    raw_line_count: int
    spans: tuple[ParagraphSpan, ...]
    status: str = "draft"
    note: str | None = None

    def __post_init__(self) -> None:
        if self.mode is not ParagraphizationMode.MANUAL_SPANS:
            raise ValueError("Paragraphization artifact mode must be manual_spans")
        if self.chapter < 1 or self.raw_line_count < 1:
            raise ValueError("Artifact chapter and raw line count must be positive")
        if not self.artifact_version.strip() or self.status not in {"draft", "confirmed"}:
            raise ValueError("Artifact version and draft/confirmed status are required")
        if len(self.raw_sha256) != 64:
            raise ValueError("Artifact raw checksum must be SHA-256")


def artifact_to_dict(artifact: ParagraphizationArtifact) -> dict[str, Any]:
    return {
        "schema_version": artifact.schema_version,
        "artifact_version": artifact.artifact_version,
        "status": artifact.status,
        "work_id": artifact.work_id,
        "source_id": str(artifact.source_id),
        "chapter": artifact.chapter,
        "mode": artifact.mode.value,
        "raw_sha256": artifact.raw_sha256,
        "raw_line_count": artifact.raw_line_count,
        "spans": [
            {
                "start_line": span.start_line,
                "end_line": span.end_line,
                "disposition": span.disposition.value,
                "reason": span.reason.value if span.reason else None,
                "note": span.note,
            }
            for span in artifact.spans
        ],
        "note": artifact.note,
    }


def artifact_from_dict(payload: dict[str, Any]) -> ParagraphizationArtifact:
    spans = tuple(
        ParagraphSpan(
            start_line=int(item["start_line"]), end_line=int(item["end_line"]),
            disposition=SpanDisposition(str(item.get("disposition", "paragraph"))),
            reason=(ExclusionReason(str(item["reason"])) if item.get("reason") else None),
            note=item.get("note"),
        )
        for item in payload.get("spans", [])
    )
    return ParagraphizationArtifact(
        schema_version=str(payload["schema_version"]), artifact_version=str(payload["artifact_version"]),
        status=str(payload.get("status", "draft")), work_id=str(payload["work_id"]),
        source_id=SourceId(str(payload["source_id"])), chapter=int(payload["chapter"]),
        mode=ParagraphizationMode(str(payload["mode"])), raw_sha256=str(payload["raw_sha256"]),
        raw_line_count=int(payload["raw_line_count"]), spans=spans, note=payload.get("note"),
    )


def save_artifact(path: Path, artifact: ParagraphizationArtifact) -> None:
    write_json(path, artifact_to_dict(artifact))


def load_artifact(path: Path) -> ParagraphizationArtifact:
    return artifact_from_dict(read_json(path))


def artifact_file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_artifact(artifact: ParagraphizationArtifact, raw_text: str, manifest: SourceManifest, chapter: int, raw_sha256: str) -> None:
    lines = raw_text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    if artifact.status != "confirmed":
        raise ValueError("Only a confirmed paragraphization artifact may create normalized paragraphs")
    if artifact.work_id != manifest.descriptor.work_id or artifact.source_id != manifest.descriptor.source_id or artifact.chapter != chapter:
        raise ValueError("Paragraphization artifact coordinates do not match source manifest")
    if artifact.raw_sha256 != raw_sha256 or artifact.raw_line_count != len(lines):
        raise ValueError("Paragraphization artifact does not match immutable raw text")
    previous_end = 0
    covered: set[int] = set()
    for span in artifact.spans:
        if span.end_line > len(lines):
            raise ValueError(f"Paragraphization span ends after raw source: {span.end_line}")
        if span.start_line <= previous_end:
            raise ValueError("Paragraphization spans must be monotonic, non-overlapping and non-duplicating")
        previous_end = span.end_line
        covered.update(range(span.start_line, span.end_line + 1))
    missing_content = [index for index, line in enumerate(lines, start=1) if line.strip() and index not in covered]
    if missing_content:
        raise ValueError(f"Paragraphization loses content lines: {missing_content}")


def register_artifact(manifest: SourceManifest, manifest_path: Path, artifact_path: Path, chapter: int, paths: PathPolicy) -> SourceManifest:
    if manifest.descriptor.paragraphization_mode is not ParagraphizationMode.MANUAL_SPANS:
        raise ValueError("Only a manual_spans source may register a paragraphization artifact")
    source = manifest.chapter(chapter)
    raw_path = paths.resolve(source.raw_location)
    raw_text = raw_path.read_text(encoding="utf-8")
    artifact = load_artifact(artifact_path)
    validate_artifact(artifact, raw_text, manifest, chapter, source.sha256)
    artifact_hash = artifact_file_sha256(artifact_path)
    updated = manifest.with_paragraphization(
        chapter, paths.relative(artifact_path), artifact_hash, artifact.artifact_version,
    )
    save_manifest(manifest_path, updated)
    return updated
