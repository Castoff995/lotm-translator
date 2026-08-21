"""Deterministic normalization from a registered EPUB DOM artifact."""
from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path

from .. import NORMALIZED_SCHEMA_VERSION
from ..domain import (
    Chapter, ChapterId, EpubDomFragment, Paragraph, ParagraphId, ParagraphType,
    Provenance, ProvenanceLocationKind, SourceManifest,
)
from ..infrastructure.paths import PathPolicy
from ..ingest.epub import document_bytes, load_epub_package
from .epub_artifact import (
    artifact_file_sha256, load_artifact, readable_text, resolve_dom_path,
    validate_artifact, _parse_document,
)
from .service import _classify


def _normalize(raw: str) -> str:
    value = unicodedata.normalize("NFC", raw.replace("\r\n", "\n").replace("\r", "\n"))
    return " ".join(line.strip() for line in value.splitlines() if line.strip())


def normalize_epub_manifest_chapter(
    manifest: SourceManifest, manifest_path: Path, chapter_number: int, paths: PathPolicy,
) -> Chapter:
    source = manifest.chapter(chapter_number)
    raw_path = paths.resolve(source.raw_location)
    raw_digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    if raw_digest != source.sha256:
        raise ValueError(f"Raw checksum mismatch; immutable EPUB changed: {raw_path}")
    if not source.paragraphization_artifact or not source.paragraphization_sha256 or not source.paragraphization_version:
        raise ValueError("epub_structure source has no confirmed registered paragraphization artifact")
    artifact_path = paths.resolve(source.paragraphization_artifact)
    artifact_digest = artifact_file_sha256(artifact_path)
    if artifact_digest != source.paragraphization_sha256:
        raise ValueError("Registered EPUB paragraphization artifact changed")
    artifact = load_artifact(artifact_path)
    if artifact.status != "confirmed" or artifact.artifact_version != source.paragraphization_version:
        raise ValueError("Registered EPUB artifact status/version mismatch")
    package = load_epub_package(raw_path)
    validate_artifact(artifact, package, manifest, chapter_number)

    roots: dict[str, tuple[bytes, object]] = {}
    chapter_id = ChapterId(manifest.descriptor.work_id, chapter_number)
    paragraphs: list[Paragraph] = []
    for index, group in enumerate(artifact.paragraphs, start=1):
        raw_parts: list[str] = []
        provenance: list[EpubDomFragment] = []
        structural_types: set[str] = set()
        source_tags: list[str] = []
        has_ruby = False
        for ref in group:
            if ref.document_href not in roots:
                data = document_bytes(package, ref.document_href)
                roots[ref.document_href] = (data, _parse_document(data, ref.document_href))
            data, root = roots[ref.document_href]
            element = resolve_dom_path(root, ref.dom_path)
            text = readable_text(element)
            if ref.start_offset is not None:
                text = text[ref.start_offset:ref.end_offset]
            raw_parts.append(text)
            source_tags.append(ref.element_tag)
            has_ruby = has_ruby or ref.has_ruby
            if ref.structural_type:
                structural_types.add(ref.structural_type)
            provenance.append(EpubDomFragment(
                ref.document_href, ref.spine_index, ref.dom_path, ref.element_tag,
                ref.document_sha256, ref.fragment_sha256, ref.element_id,
                ref.start_offset, ref.end_offset,
            ))
        raw_text = "\n".join(raw_parts)
        inferred_type, metadata = _classify(raw_text)
        paragraph_type = (
            ParagraphType.HEADING if "heading" in structural_types else
            ParagraphType.FOOTNOTE if "footnote" in structural_types else inferred_type
        )
        metadata.update({
            "source_tags": ",".join(source_tags),
            "dom_fragment_count": len(group),
            "has_ruby_annotation": has_ruby,
        })
        paragraphs.append(Paragraph(
            ParagraphId(manifest.descriptor.work_id, manifest.descriptor.source_id, chapter_number, index),
            manifest.descriptor.source_id, manifest.descriptor.language, chapter_id, index,
            raw_text, _normalize(raw_text), paragraph_type, metadata,
            Provenance(
                source_manifest=str(manifest_path), raw_location=source.raw_location,
                raw_sha256=source.sha256, normalization_version=manifest.descriptor.normalization_version,
                location_kind=ProvenanceLocationKind.EPUB_DOM, epub_fragments=tuple(provenance),
                raw_paragraph_index=index,
                paragraphization_artifact=source.paragraphization_artifact,
                paragraphization_sha256=source.paragraphization_sha256,
                paragraphization_version=source.paragraphization_version,
            ),
        ))
    return Chapter(
        NORMALIZED_SCHEMA_VERSION, chapter_id, manifest.descriptor.source_id,
        manifest.descriptor.language, tuple(paragraphs), {
            "normalization_version": manifest.descriptor.normalization_version,
            "paragraphization_mode": "epub_structure",
            "paragraphization_artifact": source.paragraphization_artifact,
            "paragraphization_sha256": source.paragraphization_sha256,
            "paragraphization_version": source.paragraphization_version,
            "raw_epub_sha256": source.sha256,
            "extraction_profile": artifact.extraction_profile,
            "toc_label": artifact.toc_label,
            "structural_separator_count": len(artifact.structural_events),
        },
    )
