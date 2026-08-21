"""Runtime workspace for deterministic human review backed by an ignored session."""
from __future__ import annotations

import hashlib
import threading
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import GOLD_SCHEMA_VERSION
from ..domain import Chapter, Language, Paragraph
from ..gold.io import gold_document_sha256, load_gold, save_gold
from ..gold.model import (
    BoundaryDecision, GoldAlignmentSide, GoldAlignmentUnit, GoldBoundary,
    GoldChapter, GoldFlag, GoldGap, GoldStatus, ParagraphDisposition,
    ParagraphDispositionReason, alignment_unit_id,
)
from ..gold.validation import GoldValidationError, validate_gold_chapter
from ..infrastructure.corpus_io import load_chapter
from ..infrastructure.paths import PathPolicy
from .compatibility import (
    REVIEW_APP_VERSION, REVIEW_SEMANTICS_VERSION, SESSION_SCHEMA_VERSION,
    ReviewCompatibilityError, ensure_supported,
)
from .session_io import archive_session, load_session, save_session
from .session_model import ReviewSessionDocument, ReviewSessionStatus, ReviewSourceSnapshot


LANGUAGE_ORDER = (Language.ZH, Language.EN, Language.RU)


class ReviewError(ValueError):
    """A user-facing error that must not mutate session or Gold unexpectedly."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _side(unit: GoldAlignmentUnit, language: Language) -> GoldAlignmentSide:
    return getattr(unit, language.value)


def _paragraph_payload(paragraph: Paragraph) -> dict[str, Any]:
    provenance = paragraph.provenance
    return {
        "id": str(paragraph.id), "index": paragraph.index,
        "type": paragraph.paragraph_type.value,
        "normalized_text": paragraph.normalized_text, "raw_text": paragraph.raw_text,
        "metadata": paragraph.metadata,
        "provenance": None if provenance is None else {
            "source_manifest": provenance.source_manifest,
            "raw_location": provenance.raw_location, "raw_sha256": provenance.raw_sha256,
            "start_line": provenance.start_line, "end_line": provenance.end_line,
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


class ReviewWorkspace:
    """Application service whose ordinary mutations autosave an ignored session only."""

    def __init__(self, gold_path: Path, root: Path) -> None:
        self.gold_path = gold_path.resolve()
        self.root = root.resolve()
        self.paths = PathPolicy(self.root)
        self._lock = threading.RLock()
        self.session_document: ReviewSessionDocument | None = None
        self.session_path: Path | None = None
        self.publication_recovered = False

        baseline = load_gold(self.gold_path)
        if baseline.schema_version != GOLD_SCHEMA_VERSION:
            raise ReviewError(
                f"Unsupported Gold schema {baseline.schema_version!r}; expected {GOLD_SCHEMA_VERSION!r}"
            )
        self.gold = baseline
        self.chapters = self._load_and_verify_sources(baseline)
        self.chapter_by_language = {chapter.language: chapter for chapter in self.chapters}
        self._require_trilingual_sources()

        if baseline.status is GoldStatus.CONFIRMED:
            self.cursors = self._validate_partial(baseline)
            return
        active_path = self.paths.active_review_session(baseline.chapter.work_id, baseline.chapter.number)
        if active_path.exists():
            self._resume(active_path, baseline)
        else:
            self._create(active_path, baseline)

    @property
    def read_only(self) -> bool:
        if self.gold.status is GoldStatus.CONFIRMED:
            return True
        return self.session_document is None or self.session_document.status is not ReviewSessionStatus.ACTIVE

    def _require_trilingual_sources(self) -> None:
        if set(self.chapter_by_language) != set(LANGUAGE_ORDER) or len(self.chapters) != len(LANGUAGE_ORDER):
            found = ", ".join(chapter.language.value for chapter in self.chapters)
            raise ReviewError(f"Review requires exactly one zh, en and ru source; found: {found}")

    def _stored_path(self, path: Path) -> str:
        try:
            return self.paths.relative(path)
        except ValueError:
            return str(path.resolve())

    def _resolved_target(self, stored: str) -> Path:
        return self.paths.resolve(stored).resolve()

    def _load_and_verify_sources(self, gold: GoldChapter) -> tuple[Chapter, ...]:
        chapters: list[Chapter] = []
        for source in gold.sources:
            path = self.paths.resolve(source.normalized_path).resolve()
            if not path.is_file():
                raise ReviewError(f"Normalized source is missing: {source.source_id} ({path})")
            actual = _sha256(path)
            if actual != source.normalized_sha256:
                raise ReviewError(
                    "Review session source mismatch. The normalized source changed after this "
                    f"review baseline was created: {source.source_id}. Human annotations are not automatically transferable."
                )
            chapter = load_chapter(path)
            if chapter.source != source.source_id or chapter.language != source.language:
                raise ReviewError(f"Normalized source identity mismatch for {source.source_id}")
            if chapter.id != gold.chapter:
                raise ReviewError(
                    f"Normalized chapter mismatch for {source.source_id}: expected {gold.chapter}, found {chapter.id}"
                )
            chapters.append(chapter)
        return tuple(chapters)

    def _source_snapshot(self, gold: GoldChapter, chapters: tuple[Chapter, ...]) -> tuple[ReviewSourceSnapshot, ...]:
        by_source = {str(item.source): item for item in chapters}
        return tuple(
            ReviewSourceSnapshot(
                source.source_id, source.language, source.normalized_path, source.normalized_sha256,
                by_source[str(source.source_id)].schema_version,
                len(by_source[str(source.source_id)].paragraphs),
            )
            for source in gold.sources
        )

    def _verify_snapshot(self, document: ReviewSessionDocument, chapters: tuple[Chapter, ...]) -> None:
        if self._source_snapshot(document.working_gold, chapters) != document.source_snapshot:
            raise ReviewError(
                "Review session source mismatch. Source identity, hashes, schema, or paragraph counts changed. "
                "Human annotations are not automatically transferable."
            )

    def _create(self, path: Path, baseline: GoldChapter) -> None:
        now = _now()
        document = ReviewSessionDocument(
            SESSION_SCHEMA_VERSION, REVIEW_SEMANTICS_VERSION, str(uuid.uuid4()),
            ReviewSessionStatus.ACTIVE, REVIEW_APP_VERSION, REVIEW_APP_VERSION,
            now, now, self._stored_path(self.gold_path), baseline.schema_version,
            _sha256(self.gold_path), baseline.chapter.work_id, baseline.chapter.number,
            self._source_snapshot(baseline, self.chapters), baseline,
        )
        save_session(path, document)
        self.session_document, self.session_path = document, path
        self.gold = baseline
        self.cursors = self._validate_partial(baseline)

    def _resume(self, path: Path, baseline: GoldChapter) -> None:
        try:
            document = load_session(path)
            ensure_supported(document)
        except (ReviewCompatibilityError, ValueError, KeyError, TypeError) as error:
            raise ReviewError(str(error)) from error
        if document.status is not ReviewSessionStatus.ACTIVE:
            raise ReviewError(f"Active-session path contains non-active status: {document.status.value}")
        if self._resolved_target(document.target_gold_path) != self.gold_path:
            raise ReviewError("Active review session targets a different Gold path")
        if (document.work_id, document.chapter) != (baseline.chapter.work_id, baseline.chapter.number):
            raise ReviewError("Active review session target work/chapter does not match Gold")
        if document.target_gold_schema_version != baseline.schema_version:
            raise ReviewError("Active review session target Gold schema does not match baseline")
        if document.working_gold.sources != baseline.sources:
            raise ReviewError(
                "Review session source mismatch. The session source references differ from the "
                "tracked Gold baseline; human annotations are not automatically transferable."
            )
        self._verify_snapshot(document, self.chapters)

        self.gold = document.working_gold
        self.chapters = self._load_and_verify_sources(document.working_gold)
        self.chapter_by_language = {chapter.language: chapter for chapter in self.chapters}
        self._require_trilingual_sources()
        self._verify_snapshot(document, self.chapters)
        cursors = self._validate_partial(document.working_gold)
        current_gold_hash = _sha256(self.gold_path)

        if document.publication_expected_gold_sha256 and current_gold_hash == document.publication_expected_gold_sha256:
            if gold_document_sha256(document.working_gold) != document.publication_expected_gold_sha256:
                raise ReviewError("Publication recovery hash does not match session working Gold")
            try:
                validate_gold_chapter(document.working_gold, self.chapters)
            except GoldValidationError as error:
                raise ReviewError(f"Recoverable publication failed full validation: {error}") from error
            self.session_document, self.session_path, self.cursors = document, path, cursors
            self._finalize_published(current_gold_hash, recovered=True)
            return
        if current_gold_hash != document.base_gold_sha256:
            raise ReviewError(
                "Gold baseline conflict. The target Gold changed after this review session was created; "
                "writable resume and publication are blocked."
            )
        updated = replace(document, last_opened_with_reviewer=REVIEW_APP_VERSION, updated_at=_now())
        save_session(path, updated)
        self.session_document, self.session_path, self.cursors = updated, path, cursors

    def recheck_integrity(self) -> None:
        chapters = self._load_and_verify_sources(self.gold)
        if self.session_document is not None:
            self._verify_snapshot(self.session_document, chapters)

    def _validate_partial(self, gold: GoldChapter) -> dict[Language, int]:
        issues: list[str] = []
        declared = {str(item.source_id): item for item in gold.sources}
        supplied = {str(item.source): item for item in self.chapters}
        if set(declared) != set(supplied):
            issues.append("Gold source references and normalized chapters differ")
        paragraph_lookup = {str(p.id): p for chapter in self.chapters for p in chapter.paragraphs}
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
        if self.gold.status is GoldStatus.CONFIRMED:
            raise ReviewError("Confirmed Gold is read-only; explicit reopen workflow is not implemented")
        if self.session_document is None or self.session_document.status is not ReviewSessionStatus.ACTIVE:
            raise ReviewError("Review session is not active or writable")

    def _autosave(self, candidate: GoldChapter, cursors: dict[Language, int] | None = None) -> None:
        self._ensure_writable()
        assert self.session_document is not None and self.session_path is not None
        updated = replace(self.session_document, working_gold=candidate, updated_at=_now())
        save_session(self.session_path, updated)
        self.session_document, self.gold = updated, candidate
        if cursors is not None:
            self.cursors = cursors

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
            alignment_unit_id(self.gold.chapter, len(self.gold.alignment_units) + 1),
            built[Language.ZH], built[Language.EN], built[Language.RU],
            tuple(flags), _optional_text(payload.get("note")),
        )

    def preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return self._unit_payload(self.build_unit(payload), include_text=True)

    def confirm(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._ensure_writable()
            candidate = replace(self.gold, alignment_units=self.gold.alignment_units + (self.build_unit(payload),))
            cursors = self._validate_partial(candidate)
            self._autosave(candidate, cursors)
            return self.snapshot()

    def disposition(self, language_value: str, reason_value: str, note: str | None = None) -> dict[str, Any]:
        with self._lock:
            self._ensure_writable()
            try:
                language, reason = Language(language_value), ParagraphDispositionReason(reason_value)
            except ValueError as error:
                raise ReviewError(str(error)) from error
            chapter = self.chapter_by_language.get(language)
            if chapter is None:
                raise ReviewError(f"No review source for language {language.value}")
            cursor = self.cursors[language]
            if cursor >= len(chapter.paragraphs):
                raise ReviewError(f"No remaining {language.value} paragraph to disposition")
            try:
                disposition = ParagraphDisposition(
                    chapter.paragraphs[cursor].id, reason, _optional_text(note), len(self.gold.alignment_units),
                )
            except ValueError as error:
                raise ReviewError(str(error)) from error
            candidate = replace(self.gold, paragraph_dispositions=self.gold.paragraph_dispositions + (disposition,))
            cursors = self._validate_partial(candidate)
            self._autosave(candidate, cursors)
            return self.snapshot()

    def undo(self) -> dict[str, Any]:
        with self._lock:
            self._ensure_writable()
            count, dispositions = len(self.gold.alignment_units), self.gold.paragraph_dispositions
            if dispositions and dispositions[-1].after_alignment_unit == count:
                candidate = replace(self.gold, paragraph_dispositions=dispositions[:-1])
            elif self.gold.alignment_units:
                units = self.gold.alignment_units[:-1]
                valid_after = {unit.id for unit in units[:-1]}
                candidate = replace(
                    self.gold, alignment_units=units,
                    boundaries=tuple(item for item in self.gold.boundaries if item.after in valid_after),
                )
            else:
                raise ReviewError("There is no review action to undo")
            cursors = self._validate_partial(candidate)
            self._autosave(candidate, cursors)
            return self.snapshot()

    def rollback(self, keep_units: int) -> dict[str, Any]:
        with self._lock:
            self._ensure_writable()
            if keep_units < 0 or keep_units > len(self.gold.alignment_units):
                raise ReviewError("Rollback target is outside the existing unit range")
            units = self.gold.alignment_units[:keep_units]
            dispositions = tuple(item for item in self.gold.paragraph_dispositions if item.after_alignment_unit < keep_units)
            valid_after = {unit.id for unit in units[:-1]}
            candidate = replace(
                self.gold, alignment_units=units, paragraph_dispositions=dispositions,
                boundaries=tuple(item for item in self.gold.boundaries if item.after in valid_after),
            )
            cursors = self._validate_partial(candidate)
            self._autosave(candidate, cursors)
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
            candidate = replace(self.gold, boundaries=tuple(by_id[key] for key in internal_ids if key in by_id))
            self._validate_partial(candidate)
            self._autosave(candidate)
            return self.snapshot()

    def finish(self) -> dict[str, Any]:
        with self._lock:
            self.recheck_integrity()
            try:
                validate_gold_chapter(self.gold, self.chapters)
            except GoldValidationError as error:
                raise ReviewError(str(error)) from error
            return {
                "valid": True,
                "message": "Full Gold validation passed; session is ready for explicit publication.",
                "summary": self._publication_summary(),
            }

    def _publication_summary(self) -> dict[str, Any]:
        return {
            "target_gold": str(self.gold_path), "work_id": self.gold.chapter.work_id,
            "chapter": self.gold.chapter.number, "alignment_units": len(self.gold.alignment_units),
            "paragraph_dispositions": len(self.gold.paragraph_dispositions),
            "boundaries": len(self.gold.boundaries), "source_hashes_verified": True,
            "gold_status_unchanged": self.gold.status.value,
        }

    def publish(self) -> dict[str, Any]:
        with self._lock:
            self._ensure_writable()
            assert self.session_document is not None and self.session_path is not None
            try:
                ensure_supported(self.session_document)
            except ReviewCompatibilityError as error:
                raise ReviewError(str(error)) from error
            self.recheck_integrity()
            if _sha256(self.gold_path) != self.session_document.base_gold_sha256:
                raise ReviewError(
                    "Gold baseline conflict. The target Gold changed after this session was created; publication is blocked."
                )
            self._validate_partial(self.gold)
            try:
                validate_gold_chapter(self.gold, self.chapters)
            except GoldValidationError as error:
                raise ReviewError(str(error)) from error
            expected_hash, started = gold_document_sha256(self.gold), _now()
            intent = replace(
                self.session_document, publication_expected_gold_sha256=expected_hash,
                publication_started_at=started, updated_at=started,
            )
            save_session(self.session_path, intent)
            self.session_document = intent
            save_gold(self.gold_path, self.gold)
            actual_hash = _sha256(self.gold_path)
            if actual_hash != expected_hash:
                raise ReviewError("Atomic Gold publication produced an unexpected hash")
            self._finalize_published(actual_hash, recovered=False)
            result = self.snapshot()
            result["publication"] = {"published": True, "recovered": False, "gold_sha256": actual_hash}
            return result

    def _finalize_published(self, digest: str, recovered: bool) -> None:
        assert self.session_document is not None and self.session_path is not None
        now = _now()
        final = replace(
            self.session_document, status=ReviewSessionStatus.PUBLISHED,
            published_gold_sha256=digest, published_at=now, updated_at=now,
        )
        archive = self.paths.archived_review_session(final.work_id, final.chapter, final.session_id, final.status.value)
        self.session_path = archive_session(self.session_path, archive, final)
        self.session_document = final
        self.gold = load_gold(self.gold_path)
        self.cursors = self._validate_partial(self.gold)
        self.publication_recovered = recovered

    def discard(self) -> dict[str, Any]:
        with self._lock:
            self._ensure_writable()
            assert self.session_document is not None and self.session_path is not None
            now = _now()
            final = replace(self.session_document, status=ReviewSessionStatus.DISCARDED, updated_at=now)
            archive = self.paths.archived_review_session(final.work_id, final.chapter, final.session_id, final.status.value)
            self.session_path = archive_session(self.session_path, archive, final)
            self.session_document = final
            return self.snapshot()

    def _unit_payload(self, unit: GoldAlignmentUnit, include_text: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": unit.id,
            "sides": {language.value: _side_payload(_side(unit, language)) for language in LANGUAGE_ORDER},
            "flags": [flag.value for flag in unit.flags], "note": unit.note,
        }
        if include_text:
            lookup = {str(p.id): p for chapter in self.chapters for p in chapter.paragraphs}
            result["selected"] = {
                language.value: ([] if _side(unit, language).gap else [
                    _paragraph_payload(lookup[str(pid)]) for pid in _side(unit, language).paragraphs
                ]) for language in LANGUAGE_ORDER
            }
        return result

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            totals = {language.value: len(self.chapter_by_language[language].paragraphs) for language in LANGUAGE_ORDER}
            cursors = {language.value: self.cursors[language] for language in LANGUAGE_ORDER}
            document = self.session_document
            return {
                "schema_version": self.gold.schema_version, "status": self.gold.status.value,
                "read_only": self.read_only, "work_id": self.gold.chapter.work_id,
                "chapter": self.gold.chapter.number, "gold_path": str(self.gold_path),
                "session": None if document is None else {
                    "session_id": document.session_id, "status": document.status.value,
                    "path": str(self.session_path), "autosave": "saved",
                    "session_schema_version": document.session_schema_version,
                    "review_semantics_version": document.review_semantics_version,
                    "review_app_version": REVIEW_APP_VERSION,
                    "created_with_reviewer": document.created_with_reviewer,
                    "last_opened_with_reviewer": document.last_opened_with_reviewer,
                    "created_at": document.created_at, "updated_at": document.updated_at,
                    "base_gold_sha256": document.base_gold_sha256,
                    "publication_recovered": self.publication_recovered,
                },
                "languages": [language.value for language in LANGUAGE_ORDER],
                "sources": {
                    language.value: {
                        "source_id": str(self.chapter_by_language[language].source),
                        "total": totals[language.value], "cursor": cursors[language.value],
                        "paragraphs": [_paragraph_payload(item) for item in self.chapter_by_language[language].paragraphs],
                    } for language in LANGUAGE_ORDER
                },
                "progress": {
                    "cursors": cursors, "totals": totals,
                    "units": len(self.gold.alignment_units),
                    "dispositions": len(self.gold.paragraph_dispositions),
                    "boundaries_reviewed": len(self.gold.boundaries),
                    "boundaries_required": max(0, len(self.gold.alignment_units) - 1),
                    "paragraphs_complete": all(cursors[key] == totals[key] for key in totals),
                },
                "units": [self._unit_payload(unit, include_text=True) for unit in self.gold.alignment_units],
                "dispositions": [
                    {
                        "paragraph_id": str(item.paragraph_id), "reason": item.reason.value,
                        "note": item.note, "after_alignment_unit": item.after_alignment_unit,
                        "paragraph": _paragraph_payload(next(
                            p for chapter in self.chapters for p in chapter.paragraphs if p.id == item.paragraph_id
                        )),
                    } for item in self.gold.paragraph_dispositions
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
