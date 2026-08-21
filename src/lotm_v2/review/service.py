"""Stateful application service for deterministic human Gold annotation."""
from __future__ import annotations

import hashlib
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

from .. import GOLD_SCHEMA_VERSION
from ..domain import Chapter, Language, Paragraph
from ..gold.io import load_gold, save_gold
from ..gold.model import (
    BoundaryDecision, GoldAlignmentSide, GoldAlignmentUnit, GoldBoundary,
    GoldChapter, GoldFlag, GoldGap, GoldStatus, ParagraphDisposition,
    ParagraphDispositionReason, alignment_unit_id,
)
from ..gold.validation import GoldValidationError, validate_gold_chapter
from ..infrastructure.corpus_io import load_chapter
from ..infrastructure.paths import PathPolicy


LANGUAGE_ORDER = (Language.ZH, Language.EN, Language.RU)


class ReviewError(ValueError):
    """A user-facing error that must not mutate the draft."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _side(unit: GoldAlignmentUnit, language: Language) -> GoldAlignmentSide:
    return getattr(unit, language.value)


def _paragraph_payload(paragraph: Paragraph) -> dict[str, Any]:
    provenance = paragraph.provenance
    return {
        "id": str(paragraph.id),
        "index": paragraph.index,
        "type": paragraph.paragraph_type.value,
        "normalized_text": paragraph.normalized_text,
        "raw_text": paragraph.raw_text,
        "metadata": paragraph.metadata,
        "provenance": None if provenance is None else {
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
        },
    }


def _side_payload(side: GoldAlignmentSide) -> dict[str, Any]:
    if side.gap:
        return {"gap": {"reason": side.gap.reason.value, "note": side.gap.note}}
    return {"paragraphs": [str(item) for item in side.paragraphs]}


class ReviewSession:
    """Owns one draft, validates every transition, and persists atomically."""

    def __init__(self, gold_path: Path, root: Path) -> None:
        self.gold_path = gold_path.resolve()
        self.root = root.resolve()
        self.paths = PathPolicy(self.root)
        self._lock = threading.RLock()
        self.gold = load_gold(self.gold_path)
        if self.gold.schema_version != GOLD_SCHEMA_VERSION:
            raise ReviewError(
                f"Unsupported Gold schema {self.gold.schema_version!r}; expected {GOLD_SCHEMA_VERSION!r}"
            )
        self.chapters = self._load_and_verify_sources()
        self.chapter_by_language = {chapter.language: chapter for chapter in self.chapters}
        if set(self.chapter_by_language) != set(LANGUAGE_ORDER) or len(self.chapters) != len(LANGUAGE_ORDER):
            found = ", ".join(chapter.language.value for chapter in self.chapters)
            raise ReviewError(f"Task 003 requires exactly one zh, en and ru source; found: {found}")
        self.cursors = self._validate_partial(self.gold)

    @property
    def read_only(self) -> bool:
        return self.gold.status == GoldStatus.CONFIRMED

    def _load_and_verify_sources(self) -> tuple[Chapter, ...]:
        chapters: list[Chapter] = []
        for source in self.gold.sources:
            path = self.paths.resolve(source.normalized_path).resolve()
            if not path.is_file():
                raise ReviewError(f"Normalized source is missing: {source.source_id} ({path})")
            actual = _sha256(path)
            if actual != source.normalized_sha256:
                raise ReviewError(
                    f"Source integrity mismatch for {source.source_id}: expected "
                    f"{source.normalized_sha256}, found {actual}. Annotation is blocked."
                )
            chapter = load_chapter(path)
            if chapter.source != source.source_id or chapter.language != source.language:
                raise ReviewError(f"Normalized source identity mismatch for {source.source_id}")
            if chapter.id != self.gold.chapter:
                raise ReviewError(
                    f"Normalized chapter mismatch for {source.source_id}: "
                    f"expected {self.gold.chapter}, found {chapter.id}"
                )
            chapters.append(chapter)
        return tuple(chapters)

    def recheck_integrity(self) -> None:
        self._load_and_verify_sources()

    def _validate_partial(self, gold: GoldChapter) -> dict[Language, int]:
        issues: list[str] = []
        declared = {str(item.source_id): item for item in gold.sources}
        supplied = {str(item.source): item for item in self.chapters}
        if set(declared) != set(supplied):
            issues.append("Gold source references and normalized chapters differ")
        paragraph_lookup = {
            str(paragraph.id): paragraph
            for chapter in self.chapters for paragraph in chapter.paragraphs
        }
        unit_ids: list[str] = []
        for position, unit in enumerate(gold.alignment_units, start=1):
            expected_id = alignment_unit_id(gold.chapter, position)
            if unit.id != expected_id:
                issues.append(f"Expected alignment unit ID {expected_id}, found {unit.id}")
            if unit.id in unit_ids:
                issues.append(f"Duplicate alignment unit ID: {unit.id}")
            unit_ids.append(unit.id)

        dispositions_by_anchor: dict[int, list[ParagraphDisposition]] = {}
        last_anchor = -1
        for disposition in gold.paragraph_dispositions:
            anchor = disposition.after_alignment_unit
            if anchor > len(gold.alignment_units):
                issues.append(f"Disposition anchor exceeds AlignmentUnit count: {disposition.paragraph_id}")
                continue
            if anchor < last_anchor:
                issues.append("Paragraph dispositions must have monotonic alignment anchors")
            last_anchor = anchor
            dispositions_by_anchor.setdefault(anchor, []).append(disposition)

        cursors = {language: 0 for language in LANGUAGE_ORDER}
        used: dict[str, str] = {}

        def consume(paragraph_id: object, expected_language: Language | None, fate: str) -> None:
            key = str(paragraph_id)
            previous_fate = used.get(key)
            if previous_fate:
                issues.append(f"Paragraph has multiple Gold fates ({previous_fate}, {fate}): {key}")
            used[key] = fate
            paragraph = paragraph_lookup.get(key)
            if paragraph is None:
                issues.append(f"Invalid {fate} paragraph reference: {key}")
                return
            if expected_language is not None and paragraph.language != expected_language:
                issues.append(f"Paragraph {key} is on the wrong language side")
                return
            language = paragraph.language
            expected_index = cursors[language] + 1
            if paragraph.index != expected_index:
                issues.append(
                    f"{fate} must consume the next contiguous {language.value} paragraph; "
                    f"expected {expected_index}, found {paragraph.index}"
                )
                return
            cursors[language] = paragraph.index

        for anchor in range(len(gold.alignment_units) + 1):
            for disposition in dispositions_by_anchor.get(anchor, []):
                consume(disposition.paragraph_id, None, "disposition")
            if anchor == len(gold.alignment_units):
                continue
            unit = gold.alignment_units[anchor]
            for language in LANGUAGE_ORDER:
                side = _side(unit, language)
                if side.gap:
                    continue
                for paragraph_id in side.paragraphs:
                    consume(paragraph_id, language, f"unit {unit.id}")
        after = [boundary.after for boundary in gold.boundaries]
        if len(after) != len(set(after)):
            issues.append("Duplicate semantic boundary annotation")
        valid_after = set(unit_ids[:-1])
        for unit_id in after:
            if unit_id not in valid_after:
                issues.append(f"Unexpected boundary after {unit_id}")
        if issues:
            raise ReviewError("Partial Gold validation failed:\n- " + "\n- ".join(issues))
        return cursors

    def _ensure_writable(self) -> None:
        if self.read_only:
            raise ReviewError("Confirmed Gold is read-only; explicit reopen workflow is not implemented")

    def _selection_side(self, language: Language, spec: dict[str, Any]) -> GoldAlignmentSide:
        if bool(spec.get("gap")):
            try:
                reason = GoldFlag(str(spec.get("reason", "omission")))
                return GoldAlignmentSide(gap=GoldGap(reason, _optional_text(spec.get("note"))))
            except ValueError as error:
                raise ReviewError(str(error)) from error
        try:
            count = int(spec.get("count", 1))
        except (TypeError, ValueError) as error:
            raise ReviewError(f"Selection size for {language.value} must be an integer") from error
        if count < 1:
            raise ReviewError(f"Selection size for {language.value} must be positive or GAP")
        chapter = self.chapter_by_language[language]
        start = self.cursors[language]
        selected = chapter.paragraphs[start:start + count]
        if len(selected) != count:
            raise ReviewError(
                f"Selection exceeds remaining {language.value} paragraphs "
                f"({len(chapter.paragraphs) - start} remain)"
            )
        return GoldAlignmentSide(paragraphs=tuple(item.id for item in selected))

    def build_unit(self, payload: dict[str, Any]) -> GoldAlignmentUnit:
        specs = payload.get("sides")
        if not isinstance(specs, dict):
            raise ReviewError("Request requires a sides object")
        built = {language: self._selection_side(language, dict(specs.get(language.value, {}))) for language in LANGUAGE_ORDER}
        flags: list[GoldFlag] = []
        for language in LANGUAGE_ORDER:
            if built[language].gap and built[language].gap.reason not in flags:
                flags.append(built[language].gap.reason)
        for raw_flag in payload.get("flags", []):
            flag = GoldFlag(str(raw_flag))
            if flag not in flags:
                flags.append(flag)
        return GoldAlignmentUnit(
            id=alignment_unit_id(self.gold.chapter, len(self.gold.alignment_units) + 1),
            zh=built[Language.ZH], en=built[Language.EN], ru=built[Language.RU],
            flags=tuple(flags), note=_optional_text(payload.get("note")),
        )

    def preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            unit = self.build_unit(payload)
            return self._unit_payload(unit, include_text=True)

    def confirm(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._ensure_writable()
            unit = self.build_unit(payload)
            candidate = replace(self.gold, alignment_units=self.gold.alignment_units + (unit,))
            cursors = self._validate_partial(candidate)
            save_gold(self.gold_path, candidate)
            self.gold, self.cursors = candidate, cursors
            return self.snapshot()

    def disposition(self, language_value: str, reason_value: str, note: str | None = None) -> dict[str, Any]:
        with self._lock:
            self._ensure_writable()
            try:
                language = Language(language_value)
                reason = ParagraphDispositionReason(reason_value)
            except ValueError as error:
                raise ReviewError(str(error)) from error
            if language not in self.chapter_by_language:
                raise ReviewError(f"No review source for language {language.value}")
            chapter = self.chapter_by_language[language]
            cursor = self.cursors[language]
            if cursor >= len(chapter.paragraphs):
                raise ReviewError(f"No remaining {language.value} paragraph to disposition")
            try:
                disposition = ParagraphDisposition(
                    paragraph_id=chapter.paragraphs[cursor].id,
                    reason=reason,
                    note=_optional_text(note),
                    after_alignment_unit=len(self.gold.alignment_units),
                )
            except ValueError as error:
                raise ReviewError(str(error)) from error
            candidate = replace(
                self.gold,
                paragraph_dispositions=self.gold.paragraph_dispositions + (disposition,),
            )
            cursors = self._validate_partial(candidate)
            save_gold(self.gold_path, candidate)
            self.gold, self.cursors = candidate, cursors
            return self.snapshot()

    def undo(self) -> dict[str, Any]:
        with self._lock:
            self._ensure_writable()
            unit_count = len(self.gold.alignment_units)
            dispositions = self.gold.paragraph_dispositions
            if dispositions and dispositions[-1].after_alignment_unit == unit_count:
                candidate = replace(self.gold, paragraph_dispositions=dispositions[:-1])
            elif self.gold.alignment_units:
                units = self.gold.alignment_units[:-1]
                valid_after = {unit.id for unit in units[:-1]}
                boundaries = tuple(item for item in self.gold.boundaries if item.after in valid_after)
                candidate = replace(self.gold, alignment_units=units, boundaries=boundaries)
            else:
                raise ReviewError("There is no review action to undo")
            cursors = self._validate_partial(candidate)
            save_gold(self.gold_path, candidate)
            self.gold, self.cursors = candidate, cursors
            return self.snapshot()

    def rollback(self, keep_units: int) -> dict[str, Any]:
        with self._lock:
            self._ensure_writable()
            return self._rollback(keep_units)

    def _rollback(self, keep_units: int) -> dict[str, Any]:
        if keep_units < 0 or keep_units > len(self.gold.alignment_units):
            raise ReviewError("Rollback target is outside the existing unit range")
        units = self.gold.alignment_units[:keep_units]
        dispositions = tuple(
            item for item in self.gold.paragraph_dispositions
            if item.after_alignment_unit < keep_units
        )
        valid_after = {unit.id for unit in units[:-1]}
        boundaries = tuple(item for item in self.gold.boundaries if item.after in valid_after)
        candidate = replace(
            self.gold, alignment_units=units, boundaries=boundaries,
            paragraph_dispositions=dispositions,
        )
        cursors = self._validate_partial(candidate)
        save_gold(self.gold_path, candidate)
        self.gold, self.cursors = candidate, cursors
        return self.snapshot()

    def set_boundary(self, after: str, decision: str, note: str | None = None) -> dict[str, Any]:
        with self._lock:
            self._ensure_writable()
            internal_ids = [unit.id for unit in self.gold.alignment_units[:-1]]
            if after not in internal_ids:
                raise ReviewError("Boundary must follow an existing non-final alignment unit")
            try:
                boundary = GoldBoundary(after, BoundaryDecision(decision), _optional_text(note))
            except ValueError as error:
                raise ReviewError(str(error)) from error
            by_id = {item.after: item for item in self.gold.boundaries}
            by_id[after] = boundary
            boundaries = tuple(by_id[unit_id] for unit_id in internal_ids if unit_id in by_id)
            candidate = replace(self.gold, boundaries=boundaries)
            self._validate_partial(candidate)
            save_gold(self.gold_path, candidate)
            self.gold = candidate
            return self.snapshot()

    def finish(self) -> dict[str, Any]:
        with self._lock:
            self.recheck_integrity()
            try:
                validate_gold_chapter(self.gold, self.chapters)
            except GoldValidationError as error:
                raise ReviewError(str(error)) from error
            return {"valid": True, "message": "Full Gold validation passed; status remains unchanged."}

    def _unit_payload(self, unit: GoldAlignmentUnit, include_text: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": unit.id,
            "sides": {language.value: _side_payload(_side(unit, language)) for language in LANGUAGE_ORDER},
            "flags": [flag.value for flag in unit.flags],
            "note": unit.note,
        }
        if include_text:
            lookup = {str(p.id): p for chapter in self.chapters for p in chapter.paragraphs}
            result["selected"] = {
                language.value: (
                    [] if _side(unit, language).gap else
                    [_paragraph_payload(lookup[str(pid)]) for pid in _side(unit, language).paragraphs]
                ) for language in LANGUAGE_ORDER
            }
        return result

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            totals = {language.value: len(self.chapter_by_language[language].paragraphs) for language in LANGUAGE_ORDER}
            cursors = {language.value: self.cursors[language] for language in LANGUAGE_ORDER}
            return {
                "schema_version": self.gold.schema_version,
                "status": self.gold.status.value,
                "read_only": self.read_only,
                "work_id": self.gold.chapter.work_id,
                "chapter": self.gold.chapter.number,
                "gold_path": str(self.gold_path),
                "languages": [language.value for language in LANGUAGE_ORDER],
                "sources": {
                    language.value: {
                        "source_id": str(self.chapter_by_language[language].source),
                        "total": totals[language.value],
                        "cursor": cursors[language.value],
                        "paragraphs": [_paragraph_payload(item) for item in self.chapter_by_language[language].paragraphs],
                    } for language in LANGUAGE_ORDER
                },
                "progress": {
                    "cursors": cursors,
                    "totals": totals,
                    "units": len(self.gold.alignment_units),
                    "dispositions": len(self.gold.paragraph_dispositions),
                    "boundaries_reviewed": len(self.gold.boundaries),
                    "boundaries_required": max(0, len(self.gold.alignment_units) - 1),
                    "paragraphs_complete": all(cursors[key] == totals[key] for key in totals),
                },
                "units": [self._unit_payload(unit, include_text=True) for unit in self.gold.alignment_units],
                "dispositions": [
                    {
                        "paragraph_id": str(item.paragraph_id),
                        "reason": item.reason.value,
                        "note": item.note,
                        "after_alignment_unit": item.after_alignment_unit,
                        "paragraph": _paragraph_payload(
                            next(
                                paragraph for chapter in self.chapters for paragraph in chapter.paragraphs
                                if paragraph.id == item.paragraph_id
                            )
                        ),
                    }
                    for item in self.gold.paragraph_dispositions
                ],
                "boundaries": [
                    {"after": item.after, "decision": item.decision.value, "note": item.note}
                    for item in self.gold.boundaries
                ],
            }


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
