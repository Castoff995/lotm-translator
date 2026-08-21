"""Explicit serialization boundary for manifests and normalized chapters."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..domain import (
    Chapter, ChapterId, Language, Paragraph, ParagraphId, ParagraphizationMode, ParagraphType,
    Provenance, SourceChapter, SourceDescriptor, SourceFormat, SourceId,
    SourceManifest, SourceRole,
)
from .json_io import read_json, write_json


def manifest_to_dict(manifest: SourceManifest) -> dict[str, Any]:
    descriptor = manifest.descriptor
    return {
        "schema_version": manifest.schema_version,
        "source": {
            "source_id": str(descriptor.source_id),
            "work_id": descriptor.work_id,
            "language": descriptor.language.value,
            "role": descriptor.role.value,
            "edition": descriptor.edition,
            "format": descriptor.source_format.value,
            "normalization_version": descriptor.normalization_version,
            "paragraphization_mode": descriptor.paragraphization_mode.value,
        },
        "chapters": [
            {
                "number": chapter.number, "raw_location": chapter.raw_location, "sha256": chapter.sha256,
                "paragraphization_artifact": chapter.paragraphization_artifact,
                "paragraphization_sha256": chapter.paragraphization_sha256,
                "paragraphization_version": chapter.paragraphization_version,
            }
            for chapter in manifest.chapters
        ],
    }


def manifest_from_dict(payload: dict[str, Any]) -> SourceManifest:
    source = payload["source"]
    descriptor = SourceDescriptor(
        source_id=SourceId(str(source["source_id"])), work_id=str(source["work_id"]),
        language=Language(str(source["language"])), role=SourceRole(str(source["role"])),
        edition=str(source["edition"]), source_format=SourceFormat(str(source["format"])),
        normalization_version=str(source["normalization_version"]),
        paragraphization_mode=ParagraphizationMode(str(source.get("paragraphization_mode", "blank_lines"))),
    )
    chapters = tuple(
        SourceChapter(
            int(item["number"]), str(item["raw_location"]), str(item["sha256"]),
            item.get("paragraphization_artifact"), item.get("paragraphization_sha256"),
            item.get("paragraphization_version"),
        )
        for item in payload.get("chapters", [])
    )
    return SourceManifest(str(payload["schema_version"]), descriptor, chapters)


def save_manifest(path: Path, manifest: SourceManifest) -> None:
    write_json(path, manifest_to_dict(manifest))


def load_manifest(path: Path) -> SourceManifest:
    return manifest_from_dict(read_json(path))


def _provenance_to_dict(provenance: Provenance | None) -> dict[str, Any] | None:
    if provenance is None:
        return None
    return {
        "source_manifest": provenance.source_manifest,
        "raw_location": provenance.raw_location,
        "raw_sha256": provenance.raw_sha256,
        "start_line": provenance.start_line,
        "end_line": provenance.end_line,
        "raw_paragraph_index": provenance.raw_paragraph_index,
        "normalization_version": provenance.normalization_version,
        "paragraphization_artifact": provenance.paragraphization_artifact,
        "paragraphization_sha256": provenance.paragraphization_sha256,
        "paragraphization_version": provenance.paragraphization_version,
    }


def chapter_to_dict(chapter: Chapter) -> dict[str, Any]:
    return {
        "schema_version": chapter.schema_version,
        "chapter_id": str(chapter.id),
        "work_id": chapter.id.work_id,
        "chapter": chapter.id.number,
        "source_id": str(chapter.source),
        "language": chapter.language.value,
        "metadata": chapter.metadata,
        "paragraphs": [
            {
                "id": str(paragraph.id), "index": paragraph.index,
                "raw_text": paragraph.raw_text, "normalized_text": paragraph.normalized_text,
                "paragraph_type": paragraph.paragraph_type.value,
                "metadata": paragraph.metadata, "provenance": _provenance_to_dict(paragraph.provenance),
            }
            for paragraph in chapter.paragraphs
        ],
    }


def chapter_from_dict(payload: dict[str, Any]) -> Chapter:
    chapter_id = ChapterId(str(payload["work_id"]), int(payload["chapter"]))
    source = SourceId(str(payload["source_id"]))
    language = Language(str(payload["language"]))
    paragraphs: list[Paragraph] = []
    for item in payload.get("paragraphs", []):
        raw_provenance = item.get("provenance")
        provenance = None if raw_provenance is None else Provenance(
            source_manifest=str(raw_provenance["source_manifest"]),
            raw_location=str(raw_provenance["raw_location"]),
            raw_sha256=str(raw_provenance["raw_sha256"]),
            start_line=int(raw_provenance.get("start_line", raw_provenance.get("raw_paragraph_index", 1))),
            end_line=int(raw_provenance.get("end_line", raw_provenance.get("raw_paragraph_index", 1))),
            normalization_version=str(raw_provenance["normalization_version"]),
            raw_paragraph_index=(int(raw_provenance["raw_paragraph_index"]) if raw_provenance.get("raw_paragraph_index") is not None else None),
            paragraphization_artifact=raw_provenance.get("paragraphization_artifact"),
            paragraphization_sha256=raw_provenance.get("paragraphization_sha256"),
            paragraphization_version=raw_provenance.get("paragraphization_version"),
        )
        paragraphs.append(Paragraph(
            id=ParagraphId.parse(str(item["id"])), source=source, language=language,
            chapter=chapter_id, index=int(item["index"]), raw_text=str(item["raw_text"]),
            normalized_text=str(item["normalized_text"]),
            paragraph_type=ParagraphType(str(item["paragraph_type"])),
            metadata=dict(item.get("metadata", {})), provenance=provenance,
        ))
    return Chapter(
        schema_version=str(payload["schema_version"]), id=chapter_id, source=source,
        language=language, paragraphs=tuple(paragraphs), metadata=dict(payload.get("metadata", {})),
    )


def save_chapter(path: Path, chapter: Chapter) -> None:
    write_json(path, chapter_to_dict(chapter))


def load_chapter(path: Path) -> Chapter:
    return chapter_from_dict(read_json(path))
