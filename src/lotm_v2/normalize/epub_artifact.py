"""Versioned, text-free EPUB chapter slicing and DOM paragraphization artifacts."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .. import EPUB_PARAGRAPHIZATION_SCHEMA_VERSION
from ..domain import SourceManifest
from ..infrastructure.corpus_io import save_manifest
from ..infrastructure.json_io import read_json, write_json
from ..infrastructure.paths import PathPolicy
from ..ingest.epub import EpubPackage, document_bytes, load_epub_package, require_epub_dependencies
from ..ingest.chapter_map import SourceChapterMap, parse_chapter_label


EXTRACTION_PROFILE = "epub-dom-physical-blocks/1"
_STRONG = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "figcaption"}
_CONTAINERS = {"article", "section", "div", "main", "blockquote", "aside"}
_IGNORED = {"head", "script", "style", "template", "nav", "rt", "rp"}


@dataclass(frozen=True)
class EpubSegment:
    document_href: str
    spine_index: int
    start_dom_path: str | None = None
    end_dom_path: str | None = None


@dataclass(frozen=True)
class EpubParagraphRef:
    document_href: str
    spine_index: int
    dom_path: str
    element_tag: str
    document_sha256: str
    fragment_sha256: str
    element_id: str | None = None
    start_offset: int | None = None
    end_offset: int | None = None
    structural_type: str | None = None
    has_ruby: bool = False


@dataclass(frozen=True)
class EpubParagraphizationArtifact:
    schema_version: str
    artifact_version: str
    status: str
    work_id: str
    source_id: str
    chapter: int
    raw_epub_sha256: str
    extraction_profile: str
    toc_index: int
    toc_label: str
    toc_href: str
    segments: tuple[EpubSegment, ...]
    paragraphs: tuple[tuple[EpubParagraphRef, ...], ...]
    structural_events: tuple[dict[str, Any], ...] = ()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def artifact_file_sha256(path: Path) -> str:
    return _sha(path.read_bytes())


def _parse_document(data: bytes, href: str):
    _, etree = require_epub_dependencies()
    try:
        return etree.fromstring(data, parser=etree.XMLParser(resolve_entities=False, no_network=True, recover=False))
    except Exception as error:
        raise ValueError(f"Malformed XHTML {href}: {error}") from error


def _local(element: Any) -> str:
    _, etree = require_epub_dependencies()
    return etree.QName(element).localname.lower()


def dom_path(element: Any) -> str:
    parts: list[str] = []
    current = element
    while current is not None and isinstance(current.tag, str):
        tag = _local(current)
        parent = current.getparent()
        siblings = [child for child in parent] if parent is not None else [current]
        same = [child for child in siblings if isinstance(child.tag, str) and _local(child) == tag]
        parts.append(f"{tag}[{same.index(current) + 1}]")
        current = parent
    return "/" + "/".join(reversed(parts))


def resolve_dom_path(root: Any, path: str):
    components = [part for part in path.split("/") if part]
    if not components:
        raise ValueError(f"Invalid empty DOM path: {path}")
    current = root
    for position, component in enumerate(components):
        match = re.fullmatch(r"([A-Za-z0-9_-]+)\[(\d+)\]", component)
        if not match:
            raise ValueError(f"Invalid DOM path component: {component}")
        tag, ordinal = match.group(1).lower(), int(match.group(2))
        if position == 0:
            if _local(current) != tag or ordinal != 1:
                raise ValueError(f"DOM path does not resolve: {path}")
            continue
        matches = [child for child in current if isinstance(child.tag, str) and _local(child) == tag]
        if ordinal < 1 or ordinal > len(matches):
            raise ValueError(f"DOM path does not resolve: {path}")
        current = matches[ordinal - 1]
    return current


def canonical_fragment_sha256(element: Any) -> str:
    _, etree = require_epub_dependencies()
    return _sha(etree.tostring(element, method="c14n", with_comments=False))


def readable_text(element: Any) -> str:
    """Extract display text without ruby annotations or non-reading containers."""
    chunks: list[str] = []

    def visit(node: Any) -> None:
        tag = _local(node) if isinstance(node.tag, str) else ""
        if tag in _IGNORED:
            return
        if node.text:
            chunks.append(node.text)
        for child in node:
            child_tag = _local(child) if isinstance(child.tag, str) else ""
            if child_tag == "br":
                chunks.append("\n")
            else:
                visit(child)
            if child.tail:
                chunks.append(child.tail)

    visit(element)
    text = "".join(chunks).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(" ".join(line.split()) for line in text.split("\n")).strip()


def _is_footnote(element: Any) -> bool:
    current = element
    while current is not None:
        attrs = " ".join(str(value) for value in current.attrib.values()).lower()
        if "footnote" in attrs or str(current.get("role", "")).lower() == "doc-footnote":
            return True
        current = current.getparent()
    return False


def structural_candidates(root: Any) -> list[Any]:
    candidates: list[Any] = []
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        tag = _local(element)
        if any(_local(parent) in _IGNORED for parent in element.iterancestors() if isinstance(parent.tag, str)):
            continue
        if tag in _STRONG:
            if any(_local(child) in _STRONG for child in element.iterdescendants() if isinstance(child.tag, str)):
                continue
            if readable_text(element):
                candidates.append(element)
        elif tag in _CONTAINERS:
            has_block = any(
                _local(child) in (_STRONG | _CONTAINERS)
                for child in element.iterdescendants() if isinstance(child.tag, str)
            )
            if not has_block and readable_text(element):
                candidates.append(element)
    return candidates


def _chapter_number(label: str) -> int | None:
    return parse_chapter_label(label)[0]


def _element_for_fragment(root: Any, fragment: str | None):
    if not fragment:
        return root
    matches = root.xpath("//*[@id=$value]", value=fragment)
    if len(matches) != 1:
        raise ValueError(f"Chapter anchor is missing or ambiguous: #{fragment}")
    return matches[0]


def propose_segments(package: EpubPackage, chapter: int, navigation_index: int | None = None) -> tuple[int, str, str, tuple[EpubSegment, ...]]:
    if navigation_index is not None:
        if navigation_index < 0 or navigation_index >= len(package.navigation):
            raise ValueError(f"TOC index is outside navigation: {navigation_index}")
        matches = [(navigation_index, package.navigation[navigation_index])]
        if _chapter_number(matches[0][1].label) is None:
            raise ValueError(f"TOC index does not identify a numeric chapter: {navigation_index}")
    else:
        matches = [(index, entry) for index, entry in enumerate(package.navigation) if _chapter_number(entry.label) == chapter]
    if len(matches) != 1:
        raise ValueError(f"Chapter {chapter} TOC mapping is ambiguous: {len(matches)} matches")
    nav_index, current = matches[0]
    spine_by_href = {item.href: item for item in package.spine}
    if current.href not in spine_by_href:
        raise ValueError(f"TOC chapter document is absent from spine: {current.href}")
    following = next(
        (entry for entry in package.navigation[nav_index + 1:] if _chapter_number(entry.label) is not None), None,
    )
    start_item = spine_by_href[current.href]
    if following is None:
        root = _parse_document(document_bytes(package, current.href), current.href)
        start_path = dom_path(_element_for_fragment(root, current.fragment)) if current.fragment else None
        return nav_index, current.label, current.href, (EpubSegment(current.href, start_item.index, start_path, None),)
    if following.href not in spine_by_href:
        raise ValueError(f"Next chapter document is absent from spine: {following.href}")
    end_item = spine_by_href[following.href]
    if end_item.index < start_item.index:
        raise ValueError("TOC chapter order disagrees with spine order")
    segments: list[EpubSegment] = []
    for spine_index in range(start_item.index, end_item.index + 1):
        item = package.spine[spine_index]
        if item.is_navigation or not item.linear:
            continue
        start_path = None
        end_path = None
        root = _parse_document(document_bytes(package, item.href), item.href)
        if spine_index == start_item.index and current.fragment:
            start_path = dom_path(_element_for_fragment(root, current.fragment))
        if spine_index == end_item.index:
            if following.href != current.href:
                break
            if not following.fragment:
                raise ValueError("Several chapters share one XHTML but next boundary has no anchor")
            end_path = dom_path(_element_for_fragment(root, following.fragment))
        segments.append(EpubSegment(item.href, spine_index, start_path, end_path))
    if not segments:
        raise ValueError(f"Chapter {chapter} resolved to an empty spine slice")
    return nav_index, current.label, current.href + (f"#{current.fragment}" if current.fragment else ""), tuple(segments)


def _in_segment(element: Any, all_elements: list[Any], start: Any | None, end: Any | None) -> bool:
    position = {id(item): index for index, item in enumerate(all_elements)}
    value = position[id(element)]
    return (start is None or value >= position[id(start)]) and (end is None or value < position[id(end)])


def _candidate_refs(package: EpubPackage, segments: Iterable[EpubSegment]) -> tuple[list[EpubParagraphRef], list[dict[str, Any]]]:
    refs: list[EpubParagraphRef] = []
    events: list[dict[str, Any]] = []
    for segment in segments:
        data = document_bytes(package, segment.document_href)
        root = _parse_document(data, segment.document_href)
        all_elements = [item for item in root.iter() if isinstance(item.tag, str)]
        start = resolve_dom_path(root, segment.start_dom_path) if segment.start_dom_path else None
        end = resolve_dom_path(root, segment.end_dom_path) if segment.end_dom_path else None
        for element in root.xpath("//*[local-name()='hr']"):
            if _in_segment(element, all_elements, start, end):
                events.append({"kind": "structural_separator", "document_href": segment.document_href, "dom_path": dom_path(element)})
        for element in structural_candidates(root):
            if not _in_segment(element, all_elements, start, end):
                continue
            tag = _local(element)
            structural_type = "heading" if tag in {f"h{i}" for i in range(1, 7)} else ("footnote" if _is_footnote(element) else None)
            refs.append(EpubParagraphRef(
                segment.document_href, segment.spine_index, dom_path(element), tag,
                _sha(data), canonical_fragment_sha256(element), element.get("id"),
                structural_type=structural_type,
                has_ruby=any(_local(item) == "ruby" for item in element.iter() if isinstance(item.tag, str)),
            ))
    return refs, events


def generate_artifact(book_path: Path, manifest: SourceManifest, chapter: int, navigation_index: int | None = None, chapter_map: SourceChapterMap | None = None) -> EpubParagraphizationArtifact:
    package = load_epub_package(book_path)
    if chapter_map is not None:
        mapped = chapter_map.chapter(chapter)
        if navigation_index is not None and navigation_index != mapped.toc_index:
            raise ValueError("Manual toc-index conflicts with registered Chapter Map")
        navigation_index = mapped.toc_index
    toc_index, label, toc_href, segments = propose_segments(package, chapter, navigation_index)
    refs, events = _candidate_refs(package, segments)
    if not refs:
        raise ValueError(f"Chapter {chapter} has no visible structural paragraph candidates")
    return EpubParagraphizationArtifact(
        EPUB_PARAGRAPHIZATION_SCHEMA_VERSION, "1", "draft", manifest.descriptor.work_id,
        str(manifest.descriptor.source_id), chapter, package.package_sha256, EXTRACTION_PROFILE,
        toc_index, label, toc_href, segments, tuple((ref,) for ref in refs), tuple(events),
    )


def artifact_to_dict(artifact: EpubParagraphizationArtifact) -> dict[str, Any]:
    def ref_dict(ref: EpubParagraphRef) -> dict[str, Any]:
        return {key: value for key, value in {
            "document_href": ref.document_href, "spine_index": ref.spine_index,
            "dom_path": ref.dom_path, "element_tag": ref.element_tag, "element_id": ref.element_id,
            "document_sha256": ref.document_sha256, "fragment_sha256": ref.fragment_sha256,
            "start_offset": ref.start_offset, "end_offset": ref.end_offset,
            "structural_type": ref.structural_type, "has_ruby": ref.has_ruby,
        }.items() if value is not None}
    return {
        "schema_version": artifact.schema_version, "artifact_version": artifact.artifact_version,
        "status": artifact.status, "work_id": artifact.work_id, "source_id": artifact.source_id,
        "chapter": artifact.chapter, "raw_epub_sha256": artifact.raw_epub_sha256,
        "extraction_profile": artifact.extraction_profile, "toc_index": artifact.toc_index, "toc_label": artifact.toc_label,
        "toc_href": artifact.toc_href,
        "segments": [segment.__dict__ for segment in artifact.segments],
        "paragraphs": [{"fragments": [ref_dict(ref) for ref in group]} for group in artifact.paragraphs],
        "structural_events": list(artifact.structural_events),
    }


def artifact_from_dict(payload: dict[str, Any]) -> EpubParagraphizationArtifact:
    return EpubParagraphizationArtifact(
        str(payload["schema_version"]), str(payload["artifact_version"]), str(payload["status"]),
        str(payload["work_id"]), str(payload["source_id"]), int(payload["chapter"]),
        str(payload["raw_epub_sha256"]), str(payload["extraction_profile"]), int(payload["toc_index"]),
        str(payload.get("toc_label", "")), str(payload.get("toc_href", "")),
        tuple(EpubSegment(**item) for item in payload.get("segments", [])),
        tuple(tuple(EpubParagraphRef(**ref) for ref in item["fragments"]) for item in payload.get("paragraphs", [])),
        tuple(payload.get("structural_events", [])),
    )


def save_artifact(path: Path, artifact: EpubParagraphizationArtifact) -> None:
    write_json(path, artifact_to_dict(artifact))


def load_artifact(path: Path) -> EpubParagraphizationArtifact:
    return artifact_from_dict(read_json(path))


def validate_artifact(artifact: EpubParagraphizationArtifact, package: EpubPackage, manifest: SourceManifest, chapter: int, chapter_map: SourceChapterMap | None = None) -> None:
    if artifact.schema_version != EPUB_PARAGRAPHIZATION_SCHEMA_VERSION or artifact.extraction_profile != EXTRACTION_PROFILE:
        raise ValueError("Unsupported EPUB paragraphization artifact/profile version")
    if (artifact.work_id, artifact.source_id, artifact.chapter) != (
        manifest.descriptor.work_id, str(manifest.descriptor.source_id), chapter,
    ):
        raise ValueError("EPUB artifact coordinates do not match source manifest")
    if artifact.raw_epub_sha256 != package.package_sha256:
        raise ValueError("EPUB artifact package checksum mismatch")
    if chapter_map is not None:
        mapped = chapter_map.chapter(chapter)
        expected_href = mapped.toc_href + (f"#{mapped.toc_fragment}" if mapped.toc_fragment else "")
        if (artifact.toc_index, artifact.toc_label, artifact.toc_href) != (mapped.toc_index, mapped.toc_label, expected_href):
            raise ValueError("EPUB paragraphization artifact contradicts registered Chapter Map")
    expected, _ = _candidate_refs(package, artifact.segments)
    actual = [ref for group in artifact.paragraphs for ref in group]
    expected_keys = [(ref.spine_index, ref.document_href, ref.dom_path) for ref in expected]
    actual_keys = [(ref.spine_index, ref.document_href, ref.dom_path) for ref in actual]
    actual_base_order: list[tuple[int, str, str]] = []
    for key in actual_keys:
        if not actual_base_order or actual_base_order[-1] != key:
            actual_base_order.append(key)
    if len(actual_base_order) != len(set(actual_base_order)):
        raise ValueError("EPUB paragraph references revisit an already consumed DOM element")
    if actual_base_order != expected_keys and set(actual_base_order) == set(expected_keys):
        raise ValueError("EPUB paragraph references are non-monotonic")
    if actual_base_order != expected_keys:
        raise ValueError("EPUB artifact does not cover every structural candidate exactly once")
    expected_by_key = {key: ref for key, ref in zip(expected_keys, expected)}
    refs_by_key: dict[tuple[int, str, str], list[EpubParagraphRef]] = {key: [] for key in expected_keys}
    for ref, key in zip(actual, actual_keys):
        refs_by_key[key].append(ref)
    for ref in actual:
        key = (ref.spine_index, ref.document_href, ref.dom_path)
        current = expected_by_key[key]
        if (ref.element_tag, ref.element_id, ref.document_sha256, ref.fragment_sha256) != (
            current.element_tag, current.element_id, current.document_sha256, current.fragment_sha256,
        ):
            raise ValueError(f"EPUB DOM provenance/hash mismatch: {ref.document_href} {ref.dom_path}")
        if not readable_text(resolve_dom_path(
            _parse_document(document_bytes(package, ref.document_href), ref.document_href), ref.dom_path,
        )):
            raise ValueError(f"EPUB paragraph is empty: {ref.document_href} {ref.dom_path}")
    for key, refs in refs_by_key.items():
        offset_pairs = [(ref.start_offset, ref.end_offset) for ref in refs]
        if len(refs) == 1 and offset_pairs == [(None, None)]:
            continue
        if any(start is None or end is None for start, end in offset_pairs):
            raise ValueError("Repeated DOM element coverage requires explicit offsets on every fragment")
        _, href, path = key
        root = _parse_document(document_bytes(package, href), href)
        text_length = len(readable_text(resolve_dom_path(root, path)))
        cursor = 0
        for start, end in offset_pairs:
            assert start is not None and end is not None
            if start != cursor or end <= start or end > text_length:
                raise ValueError("EPUB text offsets must be monotonic, non-overlapping and lossless")
            cursor = end
        if cursor != text_length:
            raise ValueError("EPUB text offsets silently leave source display text uncovered")


def confirm_artifact(artifact: EpubParagraphizationArtifact) -> EpubParagraphizationArtifact:
    return EpubParagraphizationArtifact(**{**artifact.__dict__, "status": "confirmed"})


def register_artifact(manifest: SourceManifest, manifest_path: Path, artifact_path: Path, chapter: int, paths: PathPolicy) -> SourceManifest:
    from ..ingest.chapter_map import load_registered_chapter_map
    artifact = load_artifact(artifact_path)
    source = manifest.chapter(chapter)
    package = load_epub_package(paths.resolve(source.raw_location))
    validate_artifact(artifact, package, manifest, chapter, load_registered_chapter_map(manifest, paths, package))
    if artifact.status == "draft":
        # Registration is the explicit human-controlled freeze action.
        artifact = confirm_artifact(artifact)
        save_artifact(artifact_path, artifact)
    elif artifact.status != "confirmed":
        raise ValueError(f"Invalid EPUB artifact status: {artifact.status}")
    digest = artifact_file_sha256(artifact_path)
    updated = manifest.with_paragraphization(chapter, paths.relative(artifact_path), digest, artifact.artifact_version)
    save_manifest(manifest_path, updated)
    return updated
