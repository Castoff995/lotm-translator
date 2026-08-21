"""Frozen mapping from source-local EPUB navigation to canonical ChapterId numbers."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any

from .. import SOURCE_CHAPTER_MAP_SCHEMA_VERSION, SOURCE_MANIFEST_SCHEMA_VERSION
from ..domain import SourceFormat, SourceManifest
from ..infrastructure.corpus_io import manifest_to_dict
from ..infrastructure.json_io import read_json, write_json_atomic
from ..infrastructure.paths import PathPolicy
from .epub import EpubPackage, load_epub_package


class ChapterMapEntryKind(str, Enum):
    CHAPTER = "chapter"
    ANCILLARY = "ancillary"
    AMBIGUOUS = "ambiguous"


class AncillaryKind(str, Enum):
    AUTHOR_NOTE = "author_note"
    ANNOUNCEMENT = "announcement"
    PARTITION_SUMMARY = "partition_summary"
    FRONT_MATTER = "front_matter"
    BACK_MATTER = "back_matter"
    OTHER = "other"


@dataclass(frozen=True)
class ChapterMapEntry:
    kind: ChapterMapEntryKind
    toc_index: int
    toc_label: str
    toc_href: str
    toc_fragment: str | None = None
    canonical_chapter: int | None = None
    source_chapter_ordinal: int | None = None
    numbering_scope: int | None = None
    local_chapter_number: int | None = None
    local_number_text: str | None = None
    ancillary_kind: AncillaryKind | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        if self.toc_index < 0 or not self.toc_label or not self.toc_href:
            raise ValueError("Invalid Chapter Map TOC locator")
        chapter_fields = (self.canonical_chapter, self.source_chapter_ordinal, self.numbering_scope)
        if self.kind is ChapterMapEntryKind.CHAPTER:
            if any(value is None or value < 1 for value in chapter_fields):
                raise ValueError("Chapter entry requires positive canonical/ordinal/scope fields")
            if self.local_chapter_number is not None and self.local_chapter_number < 1:
                raise ValueError("Local chapter number must be positive")
            if self.ancillary_kind is not None:
                raise ValueError("Chapter entry cannot have ancillary classification")
        elif self.kind is ChapterMapEntryKind.ANCILLARY:
            if any(value is not None for value in chapter_fields) or self.local_chapter_number is not None:
                raise ValueError("Ancillary entry cannot have chapter identity")
            if self.ancillary_kind is None:
                raise ValueError("Ancillary entry requires ancillary_kind")
            if self.ancillary_kind is AncillaryKind.OTHER and not (self.note or "").strip():
                raise ValueError("Ancillary kind 'other' requires a note")
        elif self.kind is ChapterMapEntryKind.AMBIGUOUS:
            if any(value is not None for value in chapter_fields):
                raise ValueError("Ambiguous entry cannot have canonical identity")


@dataclass(frozen=True)
class SourceChapterMap:
    schema_version: str
    map_version: str
    status: str
    work_id: str
    source_id: str
    raw_source_location: str
    raw_source_sha256: str
    navigation_kind: str
    entries: tuple[ChapterMapEntry, ...]
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {"draft", "confirmed"}:
            raise ValueError("Chapter Map status must be draft or confirmed")
        if not self.map_version.strip():
            raise ValueError("Chapter Map version must not be empty")

    def chapter(self, canonical: int) -> ChapterMapEntry:
        matches = [entry for entry in self.entries if entry.kind is ChapterMapEntryKind.CHAPTER and entry.canonical_chapter == canonical]
        if len(matches) != 1:
            raise KeyError(f"Canonical chapter {canonical} is absent or duplicated in Chapter Map")
        return matches[0]


_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}
_ZH_PATTERN = re.compile(r"^第([〇零一二两三四五六七八九十百千万\d]+)章")
_EN_PATTERN = re.compile(r"^Chapter\s+(\d+)\b", re.IGNORECASE)


def parse_chinese_number(text: str) -> int:
    if text.isdigit():
        return int(text)
    total = section = number = 0
    for char in text:
        if char in _DIGITS:
            number = _DIGITS[char]
        elif char in _UNITS:
            unit = _UNITS[char]
            if unit == 10000:
                total += (section + number) * unit
                section = number = 0
            else:
                section += (number or 1) * unit
                number = 0
        else:
            raise ValueError(f"Unsupported Chinese numeral: {text}")
    value = total + section + number
    if value < 1:
        raise ValueError(f"Chinese chapter number must be positive: {text}")
    return value


def parse_chapter_label(label: str) -> tuple[int | None, str | None, bool]:
    match = _EN_PATTERN.search(label)
    if match:
        return int(match.group(1)), match.group(1), False
    match = _ZH_PATTERN.search(label)
    if match:
        token = match.group(1)
        try:
            return parse_chinese_number(token), token, False
        except ValueError:
            return None, token, True
    numbered_looking = label.startswith("第") and "章" in label[:20]
    return None, None, numbered_looking


def generate_chapter_map(manifest: SourceManifest, package: EpubPackage, canonical_start: int = 1) -> SourceChapterMap:
    if manifest.descriptor.source_format is not SourceFormat.EPUB:
        raise ValueError("Chapter Map generation currently requires format=epub")
    if canonical_start < 1:
        raise ValueError("Canonical start must be positive")
    source = manifest.chapters[0] if manifest.chapters else None
    if source is None or source.sha256 != package.package_sha256:
        raise ValueError("Manifest immutable source does not match EPUB package")
    entries: list[ChapterMapEntry] = []
    diagnostics: list[str] = []
    scope = 1
    previous_local: int | None = None
    ordinal = 0
    for toc_index, nav in enumerate(package.navigation):
        local, local_text, ambiguous = parse_chapter_label(nav.label)
        if ambiguous:
            entries.append(ChapterMapEntry(
                ChapterMapEntryKind.AMBIGUOUS, toc_index, nav.label, nav.href, nav.fragment,
                note="Numbered-looking label could not be parsed",
            ))
            continue
        if local is None:
            entries.append(ChapterMapEntry(
                ChapterMapEntryKind.ANCILLARY, toc_index, nav.label, nav.href, nav.fragment,
                ancillary_kind=AncillaryKind.OTHER,
                note="Non-numeric navigation entry; explicitly reviewed during map registration",
            ))
            continue
        if previous_local is not None and local <= previous_local:
            scope += 1
            diagnostics.append(f"numbering reset at toc_index={toc_index}: {previous_local}->{local}")
        elif previous_local is not None and local != previous_local + 1:
            diagnostics.append(f"numbering gap at toc_index={toc_index}: {previous_local}->{local}")
        ordinal += 1
        entries.append(ChapterMapEntry(
            ChapterMapEntryKind.CHAPTER, toc_index, nav.label, nav.href, nav.fragment,
            canonical_chapter=canonical_start + ordinal - 1,
            source_chapter_ordinal=ordinal, numbering_scope=scope,
            local_chapter_number=local, local_number_text=local_text,
        ))
        previous_local = local
    kind = "ncx" if package.version.startswith("2") else "epub3_nav"
    return SourceChapterMap(
        SOURCE_CHAPTER_MAP_SCHEMA_VERSION, "1", "draft", manifest.descriptor.work_id,
        str(manifest.descriptor.source_id), source.raw_location, package.package_sha256,
        kind, tuple(entries), tuple(diagnostics),
    )


def chapter_map_to_dict(value: SourceChapterMap) -> dict[str, Any]:
    entries = []
    for entry in value.entries:
        payload = {"kind": entry.kind.value, "toc_index": entry.toc_index, "toc_label": entry.toc_label,
                   "toc_href": entry.toc_href, "toc_fragment": entry.toc_fragment}
        for key in ("canonical_chapter", "source_chapter_ordinal", "numbering_scope", "local_chapter_number", "local_number_text", "note"):
            item = getattr(entry, key)
            if item is not None:
                payload[key] = item
        if entry.ancillary_kind is not None:
            payload["ancillary_kind"] = entry.ancillary_kind.value
        entries.append(payload)
    return {
        "schema_version": value.schema_version, "map_version": value.map_version,
        "status": value.status, "work_id": value.work_id, "source_id": value.source_id,
        "raw_source_location": value.raw_source_location, "raw_source_sha256": value.raw_source_sha256,
        "navigation_kind": value.navigation_kind, "entries": entries,
        "diagnostics": list(value.diagnostics),
    }


def chapter_map_from_dict(payload: dict[str, Any]) -> SourceChapterMap:
    entries = tuple(ChapterMapEntry(
        kind=ChapterMapEntryKind(item["kind"]), toc_index=int(item["toc_index"]),
        toc_label=str(item["toc_label"]), toc_href=str(item["toc_href"]),
        toc_fragment=item.get("toc_fragment"), canonical_chapter=item.get("canonical_chapter"),
        source_chapter_ordinal=item.get("source_chapter_ordinal"), numbering_scope=item.get("numbering_scope"),
        local_chapter_number=item.get("local_chapter_number"), local_number_text=item.get("local_number_text"),
        ancillary_kind=AncillaryKind(item["ancillary_kind"]) if item.get("ancillary_kind") else None,
        note=item.get("note"),
    ) for item in payload["entries"])
    return SourceChapterMap(
        str(payload["schema_version"]), str(payload["map_version"]), str(payload["status"]),
        str(payload["work_id"]), str(payload["source_id"]), str(payload["raw_source_location"]),
        str(payload["raw_source_sha256"]), str(payload["navigation_kind"]), entries,
        tuple(str(item) for item in payload.get("diagnostics", [])),
    )


def save_chapter_map(path: Path, value: SourceChapterMap) -> None:
    write_json_atomic(path, chapter_map_to_dict(value))


def load_chapter_map(path: Path) -> SourceChapterMap:
    return chapter_map_from_dict(read_json(path))


def chapter_map_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_chapter_map(value: SourceChapterMap, manifest: SourceManifest, package: EpubPackage) -> None:
    if value.schema_version != SOURCE_CHAPTER_MAP_SCHEMA_VERSION:
        raise ValueError("Unsupported Source Chapter Map schema")
    if (value.work_id, value.source_id) != (manifest.descriptor.work_id, str(manifest.descriptor.source_id)):
        raise ValueError("Chapter Map coordinates do not match manifest")
    source = manifest.chapters[0] if manifest.chapters else None
    if source is None or value.raw_source_location != source.raw_location or value.raw_source_sha256 != source.sha256:
        raise ValueError("Chapter Map raw source provenance mismatch")
    if package.package_sha256 != value.raw_source_sha256:
        raise ValueError("Chapter Map raw EPUB hash mismatch")
    expected_navigation_kind = "ncx" if package.version.startswith("2") else "epub3_nav"
    if value.navigation_kind != expected_navigation_kind:
        raise ValueError("Chapter Map navigation kind does not match EPUB package")
    if len(value.entries) != len(package.navigation):
        raise ValueError("Chapter Map does not cover every navigation entry")
    indices = [entry.toc_index for entry in value.entries]
    if indices != list(range(len(package.navigation))):
        raise ValueError("Chapter Map toc_index coverage must be unique, monotonic and complete")
    for entry, nav in zip(value.entries, package.navigation):
        if (entry.toc_label, entry.toc_href, entry.toc_fragment) != (nav.label, nav.href, nav.fragment):
            raise ValueError(f"Chapter Map navigation locator mismatch at toc_index={entry.toc_index}")
    chapters = [entry for entry in value.entries if entry.kind is ChapterMapEntryKind.CHAPTER]
    canonical = [entry.canonical_chapter for entry in chapters]
    if len(canonical) != len(set(canonical)) or canonical != sorted(canonical):
        raise ValueError("Canonical chapters must be unique and monotonic")
    ordinals = [entry.source_chapter_ordinal for entry in chapters]
    if ordinals != list(range(1, len(chapters) + 1)):
        raise ValueError("source_chapter_ordinal must be contiguous and 1-based")
    local = [(entry.numbering_scope, entry.local_chapter_number) for entry in chapters if entry.local_chapter_number is not None]
    if len(local) != len(set(local)):
        raise ValueError("Duplicate source-local chapter identity")
    if value.status == "confirmed" and any(entry.kind is ChapterMapEntryKind.AMBIGUOUS for entry in value.entries):
        raise ValueError("Confirmed Chapter Map cannot contain ambiguous entries")


def load_registered_chapter_map(manifest: SourceManifest, paths: PathPolicy, package: EpubPackage) -> SourceChapterMap | None:
    if not manifest.chapter_map_artifact:
        return None
    path = paths.resolve(manifest.chapter_map_artifact)
    if chapter_map_sha256(path) != manifest.chapter_map_sha256:
        raise ValueError("Registered Chapter Map hash mismatch")
    value = load_chapter_map(path)
    if value.map_version != manifest.chapter_map_version or value.status != "confirmed":
        raise ValueError("Registered Chapter Map status/version mismatch")
    validate_chapter_map(value, manifest, package)
    return value


def register_chapter_map(manifest: SourceManifest, manifest_path: Path, map_path: Path, paths: PathPolicy) -> SourceManifest:
    package = load_epub_package(paths.resolve(manifest.chapters[0].raw_location))
    value = load_chapter_map(map_path)
    validate_chapter_map(value, manifest, package)
    if value.status == "draft":
        if any(entry.kind is ChapterMapEntryKind.AMBIGUOUS for entry in value.entries):
            raise ValueError("Cannot register Chapter Map with ambiguous entries")
        value = replace(value, status="confirmed")
        save_chapter_map(map_path, value)
    digest = chapter_map_sha256(map_path)
    updated = replace(manifest, schema_version=SOURCE_MANIFEST_SCHEMA_VERSION).with_chapter_map(
        paths.relative(map_path), digest, value.map_version,
    )
    write_json_atomic(manifest_path, manifest_to_dict(updated))
    return updated


def chapter_map_summary(value: SourceChapterMap) -> dict[str, Any]:
    chapters = [entry for entry in value.entries if entry.kind is ChapterMapEntryKind.CHAPTER]
    ancillary = [entry for entry in value.entries if entry.kind is ChapterMapEntryKind.ANCILLARY]
    ambiguous = [entry for entry in value.entries if entry.kind is ChapterMapEntryKind.AMBIGUOUS]
    scopes: list[dict[str, Any]] = []
    for scope in sorted({entry.numbering_scope for entry in chapters}):
        group = [entry for entry in chapters if entry.numbering_scope == scope]
        local_values = [entry.local_chapter_number for entry in group if entry.local_chapter_number is not None]
        scopes.append({"numbering_scope": int(scope),
                       "local_start": min(local_values) if local_values else None,
                       "local_end": max(local_values) if local_values else None,
                       "canonical_start": int(group[0].canonical_chapter),
                       "canonical_end": int(group[-1].canonical_chapter)})
    return {"source_id": value.source_id, "raw_source_sha256": value.raw_source_sha256,
            "status": value.status, "map_version": value.map_version,
            "navigation_kind": value.navigation_kind, "navigation_count": len(value.entries),
            "chapter_count": len(chapters), "ancillary_count": len(ancillary),
            "ambiguous_count": len(ambiguous), "canonical_range": ([chapters[0].canonical_chapter, chapters[-1].canonical_chapter] if chapters else None),
            "numbering_scopes": scopes, "diagnostics": list(value.diagnostics)}
