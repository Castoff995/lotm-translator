"""Transactional session service for dedicated two-source Pairwise Gold review."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
from threading import RLock
import time
from typing import Any
import uuid

from ..domain import Chapter, Paragraph, ParagraphId
from ..gold.model import GoldFlag, ParagraphDispositionReason
from ..gold.pairwise.bootstrap import (
    inspect_trilingual_bootstrap, load_validated_trilingual_bootstrap,
)
from ..gold.pairwise.io import (
    pairwise_gold_document_sha256, pairwise_gold_file_sha256,
    pairwise_gold_from_json_bytes, pairwise_gold_json_bytes,
    replace_pairwise_gold_atomic,
)
from ..gold.pairwise.model import (
    PairwiseAlignmentSide, PairwiseAlignmentUnit, PairwiseCreationMode, PairwiseGap,
    PairwiseGold, PairwiseGoldProvenance, PairwiseGoldStatus,
    PairwiseParagraphDisposition, pairwise_alignment_unit_id,
)
from ..gold.pairwise.validation import (
    PairwiseGoldValidationError, collect_pairwise_gold_issues,
    load_and_validate_pairwise_gold, validate_pairwise_gold,
)
from ..infrastructure.canonical_json import canonical_json_bytes, canonical_json_sha256
from ..infrastructure.file_lock import ExclusiveFileLock, FileLockTimeout
from ..infrastructure.paths import PathPolicy
from .compatibility import (
    PAIRWISE_REVIEW_APP_VERSION, PAIRWISE_REVIEW_SEMANTICS_VERSION,
    PAIRWISE_REVIEW_SESSION_SCHEMA_VERSION,
    SUPPORTED_PAIRWISE_GOLD_SCHEMA_VERSIONS,
    SUPPORTED_PAIRWISE_REVIEW_SEMANTICS_VERSIONS,
    SUPPORTED_PAIRWISE_REVIEW_SESSION_SCHEMA_VERSIONS,
)
from .session_io import (
    LEGACY_PAIRWISE_SESSION_MESSAGE, LegacyPairwiseReviewSessionError,
    archive_pairwise_session, load_pairwise_session, pairwise_session_metadata,
    save_pairwise_session,
)
from .session_model import (
    PairwiseRequestRecord, PairwiseReviewEvent, PairwiseReviewSession,
    PairwiseReviewSessionStatus,
)


PREVIEW_TTL_SECONDS = 15 * 60
EVENT_OPERATIONS = frozenset({
    "confirm_unit", "disposition", "rollback", "split", "merge", "edit_disposition",
})


class PairwiseReviewError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _optional(value: Any) -> str | None:
    return None if value is None or not str(value).strip() else str(value).strip()


def _canonical_payload(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(canonical_json_bytes(value).decode("utf-8"))


def _action_hash(operation: str, payload: dict[str, Any]) -> str:
    return canonical_json_sha256({"operation": operation, "payload": payload})


def _require_request_id(value: str) -> None:
    try:
        uuid.UUID(value)
    except (TypeError, ValueError) as error:
        raise PairwiseReviewError("Pairwise mutation request_id must be a UUID") from error


def _require_revision(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PairwiseReviewError("Pairwise mutation expected_revision must be non-negative")


def _paragraph_payload(paragraph: Paragraph) -> dict[str, Any]:
    return {
        "id": str(paragraph.id), "index": paragraph.index,
        "source_id": str(paragraph.source), "language": paragraph.language.value,
        "paragraph_type": paragraph.paragraph_type.value,
        "normalized_text": paragraph.normalized_text,
    }


@dataclass(frozen=True)
class _PreviewRecord:
    token: str
    session_id: str
    revision: int
    operation: str
    payload: dict[str, Any]
    candidate_sha256: str
    expires_at: float


class PairwiseReviewWorkspace:
    """Pairwise actions commit to one locked ignored session, never implicit Gold truth."""

    def __init__(self, gold_path: Path, root: Path, bootstrap_path: Path | None = None) -> None:
        self.root = root.resolve()
        self.paths = PathPolicy(self.root)
        self.gold_path = gold_path.resolve()
        self._lock = RLock()
        self._previews: dict[str, _PreviewRecord] = {}
        self._requested_bootstrap_path = (
            bootstrap_path.resolve() if bootstrap_path is not None else None
        )
        self.publication_recovered = False
        self.session_path: Path | None = None
        self.session_document: PairwiseReviewSession | None = None
        self.bootstrap: dict[str, Any] | None = None
        self.cursors = {"left": 0, "right": 0}

        initial_bytes = self.gold_path.read_bytes()
        initial = pairwise_gold_from_json_bytes(initial_bytes, str(self.gold_path))
        active = self.paths.active_pairwise_review_session(
            initial.work_id, initial.left_source.source_id, initial.right_source.source_id,
            initial.chapter,
        )
        if (
            active.exists()
            and pairwise_session_metadata(active).get("session_schema_version") == "1.0-draft"
        ):
            raise PairwiseReviewError(LEGACY_PAIRWISE_SESSION_MESSAGE)
        self.paths.require_pairwise_gold_path(
            self.gold_path, initial.left_source.source_id, initial.right_source.source_id,
            initial.chapter,
        )
        self._session_lock_path = self.paths.pairwise_resource_lock("session", active)
        self._gold_lock_path = self.paths.pairwise_resource_lock("gold", self.gold_path)

        try:
            # Global order: session lock, then target Gold lock.
            with ExclusiveFileLock(self._session_lock_path):
                with ExclusiveFileLock(self._gold_lock_path):
                    self._load_target_gold_locked()
                    active = self.paths.active_pairwise_review_session(
                        self.gold.work_id, self.gold.left_source.source_id,
                        self.gold.right_source.source_id, self.gold.chapter,
                    )
                    if self._recover_existing_terminal_archive_locked(active):
                        return
                    if self.gold.status is PairwiseGoldStatus.CONFIRMED:
                        if active.exists():
                            raise PairwiseReviewError(
                                "Confirmed Pairwise Gold has an unexpected active writable session"
                            )
                        if bootstrap_path is not None:
                            self.bootstrap = self._load_bootstrap(bootstrap_path)
                        return
                    if active.exists():
                        self._resume_locked(active, bootstrap_path)
                    elif self._terminal_archive_documents_locked():
                        # A terminal archive is the durable request receipt.  Keep
                        # session creation lazy so a restart retry can be answered
                        # before any new active session is created.
                        if bootstrap_path is not None:
                            self.bootstrap = self._load_bootstrap(bootstrap_path)
                    else:
                        self._create_locked(active, bootstrap_path)
        except FileLockTimeout as error:
            raise PairwiseReviewError(str(error)) from error

    @property
    def read_only(self) -> bool:
        return (
            self.gold.status is PairwiseGoldStatus.CONFIRMED
            or (
                self.session_document is not None
                and self.session_document.status is not PairwiseReviewSessionStatus.ACTIVE
            )
        )

    def _load_target_gold_locked(self) -> bytes:
        data = self.gold_path.read_bytes()
        gold = pairwise_gold_from_json_bytes(data, str(self.gold_path))
        self.paths.require_pairwise_gold_path(
            self.gold_path, gold.left_source.source_id, gold.right_source.source_id,
            gold.chapter,
        )
        validated = load_and_validate_pairwise_gold(gold, self.root, require_complete=False)
        self.gold = gold
        self.left, self.right = validated.left_chapter, validated.right_chapter
        self.chapters = (self.left, self.right)
        self.chapter_by_side = {"left": self.left, "right": self.right}
        return data

    def _stored_path(self, path: Path) -> str:
        try:
            stored = self.paths.relative(path)
            self.paths.resolve_repository_relative(stored)
            return stored
        except ValueError as error:
            raise PairwiseReviewError("Pairwise Review paths must remain inside project root") from error

    def _load_bootstrap(self, path: Path) -> dict[str, Any]:
        path = path.resolve()
        self._stored_path(path)
        report = inspect_trilingual_bootstrap(
            path, self.root, str(self.gold.left_source.source_id),
            str(self.gold.right_source.source_id),
        )
        _, chapters = load_validated_trilingual_bootstrap(path, self.root)
        paragraphs = {
            str(item.id): _paragraph_payload(item)
            for chapter in chapters for item in chapter.paragraphs
        }
        for item in report["items"]:
            context = item.get("third_language_context", {})
            context["paragraph_text"] = [
                paragraphs[key] for key in context.get("paragraphs", []) if key in paragraphs
            ]
        return report

    def _ensure_compatible(self, document: PairwiseReviewSession) -> None:
        if document.session_schema_version not in SUPPORTED_PAIRWISE_REVIEW_SESSION_SCHEMA_VERSIONS:
            raise PairwiseReviewError("Unsupported Pairwise Review Session schema")
        if document.review_semantics_version not in SUPPORTED_PAIRWISE_REVIEW_SEMANTICS_VERSIONS:
            raise PairwiseReviewError(
                "Unsupported Pairwise Review semantics; human work cannot be reinterpreted"
            )
        if document.target_pairwise_gold_schema_version not in SUPPORTED_PAIRWISE_GOLD_SCHEMA_VERSIONS:
            raise PairwiseReviewError("Unsupported Pairwise Gold schema in Review Session")

    def _create_locked(self, path: Path, bootstrap_path: Path | None) -> None:
        if path.exists():
            self._resume_locked(path, bootstrap_path)
            return
        now = _now()
        bootstrap = self._load_bootstrap(bootstrap_path) if bootstrap_path is not None else None
        baseline = self.gold
        if bootstrap is not None:
            provenance = PairwiseGoldProvenance(
                PairwiseCreationMode.TRILINGUAL_BOOTSTRAP,
                self._stored_path(bootstrap_path.resolve()),
                bootstrap["trilingual_gold_document_sha256"],
            )
            existing = self.gold.provenance
            if existing.creation_mode is PairwiseCreationMode.TRILINGUAL_BOOTSTRAP and existing != provenance:
                raise PairwiseReviewError("Pairwise Gold is frozen to different bootstrap provenance")
            baseline = replace(self.gold, provenance=provenance)
        document = PairwiseReviewSession(
            session_schema_version=PAIRWISE_REVIEW_SESSION_SCHEMA_VERSION,
            review_semantics_version=PAIRWISE_REVIEW_SEMANTICS_VERSION,
            session_id=str(uuid.uuid4()), status=PairwiseReviewSessionStatus.ACTIVE,
            created_with_reviewer=PAIRWISE_REVIEW_APP_VERSION,
            last_opened_with_reviewer=PAIRWISE_REVIEW_APP_VERSION,
            created_at=now, updated_at=now,
            target_pairwise_gold_path=self._stored_path(self.gold_path),
            target_pairwise_gold_schema_version=self.gold.schema_version,
            base_pairwise_gold_file_sha256=pairwise_gold_file_sha256(self.gold_path),
            work_id=self.gold.work_id, chapter=self.gold.chapter,
            pair_key=self.gold.pair_key,
            source_snapshot=(self.gold.left_source, self.gold.right_source),
            bootstrap_trilingual_gold_path=(
                self._stored_path(bootstrap_path.resolve()) if bootstrap_path is not None else None
            ),
            bootstrap_trilingual_gold_document_sha256=(
                bootstrap["trilingual_gold_document_sha256"] if bootstrap else None
            ),
            baseline_gold=baseline, revision=0, journal=(), request_history=(),
            working_gold=baseline,
        )
        self._validate_partial(baseline)
        save_pairwise_session(path, document)
        self.session_path, self.session_document = path, document
        self.gold, self.bootstrap = baseline, bootstrap
        self.cursors = self._validate_partial(baseline)

    def _validate_terminal_archive_locked(
        self, path: Path, document: PairwiseReviewSession,
        status: PairwiseReviewSessionStatus,
    ) -> tuple[PairwiseRequestRecord, PairwiseGold]:
        """Validate one terminal archive independently of retry/recovery entrypoint."""
        self._ensure_compatible(document)
        expected_path = self.paths.archived_pairwise_review_session(
            self.gold.work_id, self.gold.left_source.source_id,
            self.gold.right_source.source_id, self.gold.chapter,
            document.session_id, status.value,
        )
        if path.resolve() != expected_path.resolve():
            raise PairwiseReviewError(
                "Pairwise terminal receipt path does not match its identity"
            )
        if (
            document.status is not status
            or document.target_pairwise_gold_path != self._stored_path(self.gold_path)
            or document.work_id != self.gold.work_id
            or document.chapter != self.gold.chapter
            or document.pair_key != self.gold.pair_key
            or document.source_snapshot
            != (self.gold.left_source, self.gold.right_source)
        ):
            raise PairwiseReviewError(
                "Pairwise terminal receipt does not match its target"
            )

        replayed = self._replay_and_validate(document)
        working_hash = pairwise_gold_document_sha256(document.working_gold)
        terminal_records = tuple(
            item for item in document.request_history
            if item.operation in {"publish", "discard"}
        )
        if len(terminal_records) != 1:
            raise PairwiseReviewError(
                "Pairwise terminal receipt must contain exactly one terminal request"
            )
        terminal = terminal_records[0]
        expected_operation = {
            PairwiseReviewSessionStatus.PUBLISHED: "publish",
            PairwiseReviewSessionStatus.DISCARDED: "discard",
        }[status]
        expected_payload = (
            {"explicit_confirmation": True} if expected_operation == "publish" else {}
        )
        if (
            terminal.resulting_revision != document.revision
            or terminal.resulting_working_gold_sha256 != working_hash
            or terminal.operation != expected_operation
            or terminal.payload_sha256
            != _action_hash(expected_operation, expected_payload)
        ):
            raise PairwiseReviewError(
                "Pairwise terminal receipt result or payload semantics diverged"
            )
        if any((
            document.publication_request_id,
            document.publication_expected_revision,
            document.publication_expected_document_sha256,
            document.publication_started_at,
        )):
            raise PairwiseReviewError(
                "Pairwise terminal receipt retains an unfinished publication intent"
            )
        if status is PairwiseReviewSessionStatus.PUBLISHED:
            expected_file_hash = hashlib.sha256(
                pairwise_gold_json_bytes(document.working_gold)
            ).hexdigest()
            if document.published_gold_file_sha256 != expected_file_hash:
                raise PairwiseReviewError(
                    "Pairwise published terminal receipt hash diverged"
                )
        elif document.published_gold_file_sha256 is not None:
            raise PairwiseReviewError(
                "Discarded Pairwise terminal receipt has publication provenance"
            )
        return terminal, replayed

    def _load_validated_terminal_archive_locked(
        self, path: Path, status: PairwiseReviewSessionStatus,
    ) -> tuple[PairwiseReviewSession, PairwiseRequestRecord, PairwiseGold]:
        try:
            document = load_pairwise_session(path)
            terminal, replayed = self._validate_terminal_archive_locked(
                path, document, status,
            )
        except (LegacyPairwiseReviewSessionError, OSError, ValueError) as error:
            if isinstance(error, PairwiseReviewError):
                raise
            raise PairwiseReviewError(
                f"Invalid Pairwise terminal receipt {path}: {error}"
            ) from error
        return document, terminal, replayed

    def _recover_existing_terminal_archive_locked(self, active: Path) -> bool:
        if not active.exists():
            return False
        metadata = pairwise_session_metadata(active)
        if metadata.get("session_schema_version") == "1.0-draft":
            return False
        session_id = metadata.get("session_id")
        if not isinstance(session_id, str):
            return False
        found: list[tuple[Path, PairwiseReviewSessionStatus]] = []
        for status in (
            PairwiseReviewSessionStatus.PUBLISHED, PairwiseReviewSessionStatus.DISCARDED,
        ):
            candidate = self.paths.archived_pairwise_review_session(
                self.gold.work_id, self.gold.left_source.source_id,
                self.gold.right_source.source_id, self.gold.chapter, session_id, status.value,
            )
            if candidate.exists():
                found.append((candidate, status))
        if not found:
            return False
        if len(found) != 1:
            raise PairwiseReviewError("Ambiguous Pairwise terminal archives for active session")
        archive_path, archive_status = found[0]
        archived, terminal, replayed = self._load_validated_terminal_archive_locked(
            archive_path, archive_status,
        )
        if archived.session_id != session_id or archived.target_pairwise_gold_path != metadata.get("target_gold"):
            raise PairwiseReviewError("Pairwise terminal archive does not match stale active session")
        try:
            active_document = load_pairwise_session(active)
        except LegacyPairwiseReviewSessionError as error:
            raise PairwiseReviewError(
                "Legacy active session conflicts with a terminal Pairwise archive"
            ) from error
        self._ensure_compatible(active_document)
        if (
            active_document.status is not PairwiseReviewSessionStatus.ACTIVE
            or active_document.session_id != archived.session_id
            or active_document.work_id != archived.work_id
            or active_document.chapter != archived.chapter
            or active_document.pair_key != archived.pair_key
            or active_document.source_snapshot != archived.source_snapshot
            or active_document.target_pairwise_gold_path
            != archived.target_pairwise_gold_path
            or active_document.target_pairwise_gold_schema_version
            != archived.target_pairwise_gold_schema_version
            or active_document.base_pairwise_gold_file_sha256
            != archived.base_pairwise_gold_file_sha256
            or active_document.bootstrap_trilingual_gold_path
            != archived.bootstrap_trilingual_gold_path
            or active_document.bootstrap_trilingual_gold_document_sha256
            != archived.bootstrap_trilingual_gold_document_sha256
            or active_document.baseline_gold != archived.baseline_gold
            or active_document.journal != archived.journal
            or active_document.working_gold != archived.working_gold
            or archived.revision != active_document.revision + 1
            or archived.request_history
            != active_document.request_history + (terminal,)
        ):
            raise PairwiseReviewError(
                "Pairwise terminal archive is inconsistent with the stale active session"
            )
        working_hash = pairwise_gold_document_sha256(active_document.working_gold)
        if archived.status is PairwiseReviewSessionStatus.PUBLISHED:
            if (
                active_document.publication_request_id != terminal.request_id
                or active_document.publication_expected_revision
                != active_document.revision
                or active_document.publication_expected_document_sha256
                != working_hash
                or active_document.publication_started_at is None
            ):
                raise PairwiseReviewError(
                    "Published Pairwise terminal archive does not match active publication intent"
                )
        elif active_document.publication_request_id is not None:
            raise PairwiseReviewError(
                "Discarded Pairwise terminal archive conflicts with active publication intent"
            )
        target_bytes = self.gold_path.read_bytes()
        target_file_hash = hashlib.sha256(target_bytes).hexdigest()
        if archived.status is PairwiseReviewSessionStatus.PUBLISHED:
            if (
                archived.published_gold_file_sha256 != target_file_hash
                or pairwise_gold_from_json_bytes(target_bytes, str(self.gold_path)) != replayed
            ):
                raise PairwiseReviewError(
                    "Published Pairwise terminal archive does not match target Gold"
                )
            self.gold = replayed
        elif target_file_hash != archived.base_pairwise_gold_file_sha256:
            raise PairwiseReviewError(
                "Discarded Pairwise terminal archive does not match unchanged target Gold"
            )
        active.unlink()
        self.session_path, self.session_document = archive_path, archived
        self.cursors = self._validate_partial(self.gold)
        self.publication_recovered = archived.status is PairwiseReviewSessionStatus.PUBLISHED
        return True

    def _terminal_archive_documents_locked(
        self,
    ) -> tuple[tuple[Path, PairwiseReviewSession], ...]:
        """Strictly load target-scoped terminal archives as durable receipts."""
        active = self.paths.active_pairwise_review_session(
            self.gold.work_id, self.gold.left_source.source_id,
            self.gold.right_source.source_id, self.gold.chapter,
        )
        archive_root = active.parent / "archive"
        if not archive_root.exists():
            return ()
        found: list[tuple[Path, PairwiseReviewSession]] = []
        for status in (
            PairwiseReviewSessionStatus.PUBLISHED, PairwiseReviewSessionStatus.DISCARDED,
        ):
            for path in sorted(archive_root.glob(
                f"ch_{self.gold.chapter:04d}.*.{status.value}.json"
            )):
                document, _, _ = self._load_validated_terminal_archive_locked(
                    path, status,
                )
                found.append((path, document))
        return tuple(found)

    def _terminal_retry_locked(
        self, request_id: str, operation: str, payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return a durable terminal result before creating a new session."""
        _require_request_id(request_id)
        canonical = _canonical_payload(payload)
        matches: list[tuple[Path, PairwiseReviewSession]] = []
        for path, document in self._terminal_archive_documents_locked():
            records = tuple(
                item for item in document.request_history if item.request_id == request_id
            )
            if not records:
                continue
            if len(records) != 1:
                raise PairwiseReviewError("Ambiguous Pairwise terminal request receipt")
            record = records[0]
            if (
                record.operation != operation
                or record.payload_sha256 != _action_hash(operation, canonical)
            ):
                raise PairwiseReviewError(
                    "Pairwise request_id was already used for a different semantic action"
                )
            expected_status = {
                "publish": PairwiseReviewSessionStatus.PUBLISHED,
                "discard": PairwiseReviewSessionStatus.DISCARDED,
            }.get(operation)
            if expected_status is None or document.status is not expected_status:
                raise PairwiseReviewError(
                    "Pairwise terminal request receipt has a different operation"
                )
            matches.append((path, document))
        if not matches:
            return None
        if len(matches) != 1:
            raise PairwiseReviewError("Ambiguous Pairwise terminal request receipt")
        path, document = matches[0]
        target_file_hash = hashlib.sha256(self.gold_path.read_bytes()).hexdigest()
        expected_target_hash = (
            document.published_gold_file_sha256
            if operation == "publish"
            else document.base_pairwise_gold_file_sha256
        )
        if target_file_hash != expected_target_hash:
            raise PairwiseReviewError(
                "Pairwise terminal request receipt conflicts with current target Gold"
            )
        self.session_path, self.session_document = path, document
        self.gold = document.working_gold
        self.cursors = self._validate_partial(self.gold)
        result = self.snapshot()
        if operation == "publish":
            result["publication"] = {
                "published": True, "status": "draft", "recovered": False,
                "file_sha256": document.published_gold_file_sha256,
            }
        result["idempotent"] = True
        return result

    def _resume_locked(self, path: Path, requested_bootstrap: Path | None) -> None:
        try:
            document = load_pairwise_session(path)
        except LegacyPairwiseReviewSessionError as error:
            raise PairwiseReviewError(LEGACY_PAIRWISE_SESSION_MESSAGE) from error
        self._ensure_compatible(document)
        if document.status is not PairwiseReviewSessionStatus.ACTIVE:
            raise PairwiseReviewError("Only an active Pairwise Review Session can resume writable")
        target = self.paths.resolve_repository_relative(document.target_pairwise_gold_path)
        if target != self.gold_path:
            raise PairwiseReviewError("Pairwise Review Session targets another Gold path")
        current_sources = (self.gold.left_source, self.gold.right_source)
        if document.source_snapshot != current_sources:
            raise PairwiseReviewError(
                "Pairwise Review source identity/revision changed; writable resume rejected"
            )
        stored_bootstrap = (
            self.paths.resolve_repository_relative(document.bootstrap_trilingual_gold_path)
            if document.bootstrap_trilingual_gold_path else None
        )
        if requested_bootstrap is not None and requested_bootstrap.resolve() != stored_bootstrap:
            raise PairwiseReviewError("Pairwise Review Session is frozen to a different bootstrap artifact")
        bootstrap = self._load_bootstrap(stored_bootstrap) if stored_bootstrap else None
        if bootstrap and (
            bootstrap["trilingual_gold_document_sha256"]
            != document.bootstrap_trilingual_gold_document_sha256
        ):
            raise PairwiseReviewError("Trilingual bootstrap changed; writable resume rejected")

        target_bytes = self.gold_path.read_bytes()
        target_file_hash = hashlib.sha256(target_bytes).hexdigest()
        if document.publication_expected_document_sha256:
            target_gold = pairwise_gold_from_json_bytes(target_bytes, str(self.gold_path))
            if (
                pairwise_gold_document_sha256(target_gold)
                == document.publication_expected_document_sha256
            ):
                self.session_path, self.session_document = path, document
                self.gold, self.bootstrap = document.working_gold, bootstrap
                self.cursors = self._validate_partial(self.gold)
                self._finalize_published_locked(target_file_hash, recovered=True)
                return
            if target_file_hash == document.base_pairwise_gold_file_sha256:
                document = replace(
                    document, publication_request_id=None,
                    publication_expected_revision=None,
                    publication_expected_document_sha256=None,
                    publication_started_at=None, updated_at=_now(),
                )
                save_pairwise_session(path, document)
            else:
                raise PairwiseReviewError(
                    "Interrupted Pairwise publication conflicts with current target; session preserved"
                )
        elif target_file_hash != document.base_pairwise_gold_file_sha256:
            raise PairwiseReviewError(
                "Pairwise Gold baseline changed; writable resume rejected and session preserved"
            )
        replayed = self._replay_and_validate(document)
        updated = replace(
            document, last_opened_with_reviewer=PAIRWISE_REVIEW_APP_VERSION,
            updated_at=_now(),
        )
        save_pairwise_session(path, updated)
        self.session_path, self.session_document = path, updated
        self.gold, self.bootstrap = replayed, bootstrap
        self.cursors = self._validate_partial(replayed)

    def _verify_source_snapshots(self, document: PairwiseReviewSession) -> None:
        for source in document.source_snapshot:
            path = self.paths.require_normalized_chapter_path(
                source.normalized_path, source.source_id, document.chapter,
            )
            if hashlib.sha256(path.read_bytes()).hexdigest() != source.normalized_sha256:
                raise PairwiseReviewError(
                    "Pairwise Review normalized source changed; writable mutation rejected"
                )

    def _refresh_active_locked(self) -> PairwiseReviewSession:
        if self.session_path is None:
            if self.gold.status is PairwiseGoldStatus.CONFIRMED:
                raise PairwiseReviewError("Pairwise Review Session is not active")
            active = self.paths.active_pairwise_review_session(
                self.gold.work_id, self.gold.left_source.source_id,
                self.gold.right_source.source_id, self.gold.chapter,
            )
            if active.exists():
                self._resume_locked(active, self._requested_bootstrap_path)
            else:
                self._create_locked(active, self._requested_bootstrap_path)
        if self.session_path is None or not self.session_path.name.endswith(".active.json"):
            raise PairwiseReviewError("Pairwise Review Session is not active")
        try:
            document = load_pairwise_session(self.session_path)
        except LegacyPairwiseReviewSessionError as error:
            raise PairwiseReviewError(LEGACY_PAIRWISE_SESSION_MESSAGE) from error
        self._ensure_compatible(document)
        if document.status is not PairwiseReviewSessionStatus.ACTIVE:
            raise PairwiseReviewError("Pairwise Review Session is not active")
        self._verify_source_snapshots(document)
        replayed = self._replay_and_validate(document)
        self.session_document, self.gold = document, replayed
        self.cursors = self._validate_partial(replayed)
        return document

    def _validate_partial(self, gold: PairwiseGold) -> dict[str, int]:
        issues = list(collect_pairwise_gold_issues(
            gold, self.left, self.right, gold.left_source.normalized_sha256,
            gold.right_source.normalized_sha256, require_complete=False,
        ))
        disposition_by_anchor: dict[int, list[PairwiseParagraphDisposition]] = {}
        for item in gold.paragraph_dispositions:
            disposition_by_anchor.setdefault(item.after_alignment_unit, []).append(item)
        lookup = {
            str(item.id): (side, item)
            for side, chapter in self.chapter_by_side.items() for item in chapter.paragraphs
        }
        cursors = {"left": 0, "right": 0}
        fates: dict[str, str] = {}

        def consume(paragraph_id: object, expected_side: str | None, fate: str) -> None:
            key = str(paragraph_id)
            if key in fates:
                issues.append(f"Paragraph has multiple Pairwise Gold fates ({fates[key]}, {fate}): {key}")
            fates[key] = fate
            found = lookup.get(key)
            if found is None:
                return
            side, paragraph = found
            if expected_side is not None and side != expected_side:
                issues.append(f"Paragraph {key} is on wrong Pairwise side")
                return
            expected_index = cursors[side] + 1
            if paragraph.index != expected_index:
                issues.append(
                    f"{fate} must consume next contiguous {side} Paragraph; "
                    f"expected {expected_index}, found {paragraph.index}"
                )
                return
            cursors[side] = paragraph.index

        for anchor in range(len(gold.alignment_units) + 1):
            for disposition in disposition_by_anchor.get(anchor, []):
                consume(disposition.paragraph_id, None, "disposition")
            if anchor == len(gold.alignment_units):
                continue
            unit = gold.alignment_units[anchor]
            for side_name, side in (("left", unit.left), ("right", unit.right)):
                if side.gap:
                    continue
                for paragraph_id in side.paragraphs:
                    consume(paragraph_id, side_name, f"unit {unit.id}")
        if issues:
            raise PairwiseReviewError(
                "Partial Pairwise Gold validation failed:\n- " + "\n- ".join(issues)
            )
        return cursors

    def _side_from_event(self, value: dict[str, Any]) -> PairwiseAlignmentSide:
        if set(value) == {"paragraphs"}:
            raw = value["paragraphs"]
            if not isinstance(raw, list) or not raw:
                raise PairwiseReviewError("Journal side paragraphs must be non-empty")
            return PairwiseAlignmentSide(
                paragraphs=tuple(ParagraphId.parse(str(item)) for item in raw)
            )
        if set(value) == {"gap"}:
            gap = value["gap"]
            if not isinstance(gap, dict) or set(gap) != {"reason", "note"}:
                raise PairwiseReviewError("Journal GAP payload is invalid")
            return PairwiseAlignmentSide(
                gap=PairwiseGap(GoldFlag(str(gap["reason"])), _optional(gap["note"]))
            )
        raise PairwiseReviewError("Journal side must contain exactly paragraphs or gap")

    def _unit_from_event(self, value: dict[str, Any]) -> PairwiseAlignmentUnit:
        if set(value) != {"id", "left", "right", "note"}:
            raise PairwiseReviewError("Journal Pairwise unit payload is invalid")
        return PairwiseAlignmentUnit(
            str(value["id"]), self._side_from_event(dict(value["left"])),
            self._side_from_event(dict(value["right"])), _optional(value["note"]),
        )

    def _disposition_from_event(self, value: dict[str, Any]) -> PairwiseParagraphDisposition:
        if set(value) != {"paragraph_id", "reason", "note", "after_alignment_unit"}:
            raise PairwiseReviewError("Journal disposition payload is invalid")
        return PairwiseParagraphDisposition(
            ParagraphId.parse(str(value["paragraph_id"])),
            ParagraphDispositionReason(str(value["reason"])),
            _optional(value["note"]), int(value["after_alignment_unit"]),
        )

    @staticmethod
    def _unit_index(gold: PairwiseGold, unit_id: str) -> int:
        found = [index for index, item in enumerate(gold.alignment_units) if item.id == unit_id]
        if len(found) != 1:
            raise PairwiseReviewError(f"Pairwise AlignmentUnit missing or ambiguous: {unit_id}")
        return found[0]

    @staticmethod
    def _renumber(gold: PairwiseGold, units: tuple[PairwiseAlignmentUnit, ...]) -> tuple[PairwiseAlignmentUnit, ...]:
        return tuple(
            replace(item, id=pairwise_alignment_unit_id(
                gold.chapter_id, gold.left_source.source_id, gold.right_source.source_id, index,
            ))
            for index, item in enumerate(units, start=1)
        )

    def _split_candidate(
        self, gold: PairwiseGold, unit_id: str, first_counts: dict[str, int],
    ) -> PairwiseGold:
        index = self._unit_index(gold, unit_id)
        unit = gold.alignment_units[index]
        if unit.left.gap or unit.right.gap or unit.note:
            raise PairwiseReviewError("Safe split requires two paragraph-backed sides and no unit note")
        pieces: dict[str, tuple[PairwiseAlignmentSide, PairwiseAlignmentSide]] = {}
        for side_name, side in (("left", unit.left), ("right", unit.right)):
            count = first_counts.get(side_name)
            if (
                isinstance(count, bool) or not isinstance(count, int)
                or count < 1 or count >= len(side.paragraphs)
            ):
                raise PairwiseReviewError(
                    f"Split count for {side_name} must leave Paragraphs in both children"
                )
            pieces[side_name] = (
                PairwiseAlignmentSide(paragraphs=side.paragraphs[:count]),
                PairwiseAlignmentSide(paragraphs=side.paragraphs[count:]),
            )
        children = (
            PairwiseAlignmentUnit(unit.id, pieces["left"][0], pieces["right"][0]),
            PairwiseAlignmentUnit(unit.id, pieces["left"][1], pieces["right"][1]),
        )
        units = self._renumber(
            gold, gold.alignment_units[:index] + children + gold.alignment_units[index + 1:],
        )
        old_number = index + 1
        dispositions = tuple(
            replace(item, after_alignment_unit=item.after_alignment_unit + 1)
            if item.after_alignment_unit >= old_number else item
            for item in gold.paragraph_dispositions
        )
        return replace(gold, alignment_units=units, paragraph_dispositions=dispositions)

    def _merge_candidate(self, gold: PairwiseGold, unit_id: str) -> PairwiseGold:
        index = self._unit_index(gold, unit_id)
        if index + 1 >= len(gold.alignment_units):
            raise PairwiseReviewError("Merge with next requires a following Pairwise unit")
        first, second = gold.alignment_units[index:index + 2]
        if (
            first.left.gap or first.right.gap or second.left.gap or second.right.gap
            or first.note or second.note
        ):
            raise PairwiseReviewError("Safe merge requires paragraph-backed units without notes")
        internal_anchor = index + 1
        if any(
            item.after_alignment_unit == internal_anchor
            for item in gold.paragraph_dispositions
        ):
            raise PairwiseReviewError("Cannot merge across an anchored Pairwise disposition")
        merged = PairwiseAlignmentUnit(
            first.id,
            PairwiseAlignmentSide(paragraphs=first.left.paragraphs + second.left.paragraphs),
            PairwiseAlignmentSide(paragraphs=first.right.paragraphs + second.right.paragraphs),
        )
        units = self._renumber(
            gold, gold.alignment_units[:index] + (merged,) + gold.alignment_units[index + 2:],
        )
        dispositions = tuple(
            replace(item, after_alignment_unit=item.after_alignment_unit - 1)
            if item.after_alignment_unit > internal_anchor else item
            for item in gold.paragraph_dispositions
        )
        return replace(gold, alignment_units=units, paragraph_dispositions=dispositions)

    def _edit_disposition_candidate(
        self, gold: PairwiseGold, paragraph_id: str, reason: str, note: str | None,
    ) -> PairwiseGold:
        found = [
            index for index, item in enumerate(gold.paragraph_dispositions)
            if str(item.paragraph_id) == paragraph_id
        ]
        if len(found) != 1:
            raise PairwiseReviewError(f"Pairwise disposition missing or ambiguous: {paragraph_id}")
        index = found[0]
        edited = replace(
            gold.paragraph_dispositions[index],
            reason=ParagraphDispositionReason(reason), note=_optional(note),
        )
        if edited == gold.paragraph_dispositions[index]:
            raise PairwiseReviewError("Disposition edit makes no change")
        items = list(gold.paragraph_dispositions)
        items[index] = edited
        return replace(gold, paragraph_dispositions=tuple(items))

    def _apply_event(
        self, gold: PairwiseGold, operation: str, payload: dict[str, Any],
    ) -> PairwiseGold:
        if operation == "confirm_unit":
            unit = self._unit_from_event(dict(payload["unit"]))
            candidate = replace(gold, alignment_units=gold.alignment_units + (unit,))
        elif operation == "disposition":
            item = self._disposition_from_event(dict(payload["disposition"]))
            candidate = replace(
                gold, paragraph_dispositions=gold.paragraph_dispositions + (item,),
            )
        elif operation == "rollback":
            keep = payload.get("keep_units")
            if isinstance(keep, bool) or not isinstance(keep, int) or keep < 0 or keep > len(gold.alignment_units):
                raise PairwiseReviewError("Rollback target is outside Pairwise unit range")
            candidate = replace(
                gold, alignment_units=gold.alignment_units[:keep],
                paragraph_dispositions=tuple(
                    item for item in gold.paragraph_dispositions
                    if item.after_alignment_unit < keep
                ),
            )
        elif operation == "split":
            candidate = self._split_candidate(
                gold, str(payload["unit_id"]), dict(payload["first_counts"]),
            )
        elif operation == "merge":
            candidate = self._merge_candidate(gold, str(payload["unit_id"]))
        elif operation == "edit_disposition":
            candidate = self._edit_disposition_candidate(
                gold, str(payload["paragraph_id"]), str(payload["reason"]),
                payload.get("note"),
            )
        else:
            raise PairwiseReviewError(f"Unsupported Pairwise Review event operation: {operation}")
        self._validate_partial(candidate)
        return candidate

    def _replay_and_validate(self, document: PairwiseReviewSession) -> PairwiseGold:
        gold = document.baseline_gold
        request_by_id = {item.request_id: item for item in document.request_history}
        for expected_sequence, event in enumerate(document.journal, start=1):
            if event.sequence != expected_sequence:
                raise PairwiseReviewError("Pairwise Review journal sequence diverged")
            before = pairwise_gold_document_sha256(gold)
            if event.before_working_gold_sha256 != before:
                raise PairwiseReviewError("Pairwise Review journal before-hash chain diverged")
            record = request_by_id.get(event.request_id)
            if (
                record is None or record.operation != event.operation
                or record.payload_sha256 != _action_hash(event.operation, event.payload)
            ):
                raise PairwiseReviewError("Pairwise Review journal/request history diverged")
            gold = self._apply_event(gold, event.operation, event.payload)
            after = pairwise_gold_document_sha256(gold)
            if event.after_working_gold_sha256 != after:
                raise PairwiseReviewError("Pairwise Review journal after-hash chain diverged")
            if record.resulting_working_gold_sha256 != after:
                raise PairwiseReviewError("Pairwise Review request result hash diverged")
        if gold != document.working_gold:
            raise PairwiseReviewError(
                "Pairwise Review journal replay differs from persisted working Gold"
            )
        self._validate_partial(gold)
        return gold

    def _existing_request(
        self, document: PairwiseReviewSession, request_id: str,
        operation: str, payload: dict[str, Any],
    ) -> PairwiseRequestRecord | None:
        _require_request_id(request_id)
        expected_hash = _action_hash(operation, payload)
        found = [item for item in document.request_history if item.request_id == request_id]
        if not found:
            return None
        record = found[0]
        if record.operation != operation or record.payload_sha256 != expected_hash:
            raise PairwiseReviewError(
                "Pairwise request_id was already used for a different semantic action"
            )
        return record

    def _commit_event_locked(
        self, operation: str, payload: dict[str, Any], expected_revision: int,
        request_id: str, expected_candidate_sha256: str | None = None,
    ) -> bool:
        document = self._refresh_active_locked()
        canonical = _canonical_payload(payload)
        if self._existing_request(document, request_id, operation, canonical):
            return False
        _require_revision(expected_revision)
        if document.revision != expected_revision:
            raise PairwiseReviewError(
                f"Pairwise Review revision conflict: expected {expected_revision}, "
                f"current {document.revision}"
            )
        before = pairwise_gold_document_sha256(document.working_gold)
        candidate = self._apply_event(document.working_gold, operation, canonical)
        after = pairwise_gold_document_sha256(candidate)
        if expected_candidate_sha256 is not None and after != expected_candidate_sha256:
            raise PairwiseReviewError("Pairwise preview candidate identity diverged")
        now, revision = _now(), document.revision + 1
        event = PairwiseReviewEvent(
            len(document.journal) + 1, request_id, operation, canonical,
            before, after, now,
        )
        record = PairwiseRequestRecord(
            request_id, operation, _action_hash(operation, canonical),
            revision, after, now,
        )
        updated = replace(
            document, revision=revision, journal=document.journal + (event,),
            request_history=document.request_history + (record,),
            working_gold=candidate, updated_at=now,
        )
        self._replay_and_validate(updated)
        assert self.session_path is not None
        save_pairwise_session(self.session_path, updated)
        self.session_document, self.gold = updated, candidate
        self.cursors = self._validate_partial(candidate)
        return True

    def _register_preview(
        self, operation: str, payload: dict[str, Any], candidate: PairwiseGold,
        rendered: dict[str, Any],
    ) -> dict[str, Any]:
        assert self.session_document is not None
        now = time.monotonic()
        self._previews = {
            key: item for key, item in self._previews.items()
            if item.expires_at >= now
        }
        token = secrets.token_urlsafe(32)
        canonical = _canonical_payload(payload)
        record = _PreviewRecord(
            token, self.session_document.session_id, self.session_document.revision,
            operation, canonical, pairwise_gold_document_sha256(candidate),
            now + PREVIEW_TTL_SECONDS,
        )
        self._previews[token] = record
        return {
            "operation": operation, "preview_token": token,
            "session_revision": record.revision,
            "candidate_working_gold_sha256": record.candidate_sha256,
            **rendered,
        }

    @staticmethod
    def _require_preview_revision(
        document: PairwiseReviewSession, expected_revision: int,
    ) -> None:
        _require_revision(expected_revision)
        if document.revision != expected_revision:
            raise PairwiseReviewError(
                "Pairwise Review revision conflict: Session changed in another tab "
                f"or process. Refresh before continuing. Expected {expected_revision}, "
                f"current {document.revision}"
            )

    def _selection_side(
        self, gold: PairwiseGold, cursors: dict[str, int], side_name: str,
        spec: dict[str, Any],
    ) -> PairwiseAlignmentSide:
        if bool(spec.get("gap")):
            return PairwiseAlignmentSide(gap=PairwiseGap(
                GoldFlag(str(spec.get("reason", "omission"))), _optional(spec.get("note")),
            ))
        count = spec.get("count", 1)
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise PairwiseReviewError(f"Selection count for {side_name} must be positive or GAP")
        chapter = self.chapter_by_side[side_name]
        selected = chapter.paragraphs[cursors[side_name]:cursors[side_name] + count]
        if len(selected) != count:
            raise PairwiseReviewError(f"Selection exceeds remaining {side_name} Paragraphs")
        return PairwiseAlignmentSide(paragraphs=tuple(item.id for item in selected))

    def _build_unit(self, gold: PairwiseGold, cursors: dict[str, int], payload: dict[str, Any]) -> PairwiseAlignmentUnit:
        sides = payload.get("sides")
        if not isinstance(sides, dict):
            raise PairwiseReviewError("Pairwise unit request requires sides")
        return PairwiseAlignmentUnit(
            pairwise_alignment_unit_id(
                gold.chapter_id, gold.left_source.source_id,
                gold.right_source.source_id, len(gold.alignment_units) + 1,
            ),
            self._selection_side(gold, cursors, "left", dict(sides.get("left", {}))),
            self._selection_side(gold, cursors, "right", dict(sides.get("right", {}))),
            _optional(payload.get("note")),
        )

    def _unit_event_payload(self, unit: PairwiseAlignmentUnit) -> dict[str, Any]:
        return {"unit": self._unit_payload(unit, include_text=False)}

    @staticmethod
    def _disposition_event_payload(item: PairwiseParagraphDisposition) -> dict[str, Any]:
        return {"disposition": {
            "paragraph_id": str(item.paragraph_id), "reason": item.reason.value,
            "note": item.note, "after_alignment_unit": item.after_alignment_unit,
        }}

    def preview(self, payload: dict[str, Any], expected_revision: int) -> dict[str, Any]:
        with self._lock:
            with ExclusiveFileLock(self._session_lock_path):
                document = self._refresh_active_locked()
                self._require_preview_revision(document, expected_revision)
                unit = self._build_unit(document.working_gold, self.cursors, payload)
                event_payload = self._unit_event_payload(unit)
                candidate = self._apply_event(document.working_gold, "confirm_unit", event_payload)
                return self._register_preview(
                    "confirm_unit", event_payload, candidate,
                    {"unit": self._unit_payload(unit, include_text=True)},
                )

    def preview_disposition(
        self, side_name: str, reason: str, expected_revision: int,
        note: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            with ExclusiveFileLock(self._session_lock_path):
                document = self._refresh_active_locked()
                self._require_preview_revision(document, expected_revision)
                if side_name not in self.chapter_by_side:
                    raise PairwiseReviewError("Disposition side must be left or right")
                chapter, cursor = self.chapter_by_side[side_name], self.cursors[side_name]
                if cursor >= len(chapter.paragraphs):
                    raise PairwiseReviewError(f"No remaining {side_name} Paragraph to disposition")
                item = PairwiseParagraphDisposition(
                    chapter.paragraphs[cursor].id, ParagraphDispositionReason(reason),
                    _optional(note), len(document.working_gold.alignment_units),
                )
                event_payload = self._disposition_event_payload(item)
                candidate = self._apply_event(document.working_gold, "disposition", event_payload)
                return self._register_preview(
                    "disposition", event_payload, candidate,
                    {
                        "side": side_name,
                        "disposition": event_payload["disposition"],
                        "paragraph": _paragraph_payload(chapter.paragraphs[cursor]),
                    },
                )

    def preview_split(
        self, unit_id: str, first_counts: dict[str, int], expected_revision: int,
    ) -> dict[str, Any]:
        with self._lock:
            with ExclusiveFileLock(self._session_lock_path):
                document = self._refresh_active_locked()
                self._require_preview_revision(document, expected_revision)
                payload = _canonical_payload({
                    "unit_id": unit_id, "first_counts": first_counts,
                })
                candidate = self._apply_event(document.working_gold, "split", payload)
                index = self._unit_index(document.working_gold, unit_id)
                return self._register_preview(
                    "split", payload, candidate,
                    {
                        "old": self._unit_payload(
                            document.working_gold.alignment_units[index], True,
                        ),
                        "children": [
                            self._unit_payload(item, True)
                            for item in candidate.alignment_units[index:index + 2]
                        ],
                    },
                )

    def preview_merge(self, unit_id: str, expected_revision: int) -> dict[str, Any]:
        with self._lock:
            with ExclusiveFileLock(self._session_lock_path):
                document = self._refresh_active_locked()
                self._require_preview_revision(document, expected_revision)
                payload = {"unit_id": unit_id}
                candidate = self._apply_event(document.working_gold, "merge", payload)
                index = self._unit_index(document.working_gold, unit_id)
                return self._register_preview(
                    "merge", payload, candidate,
                    {
                        "old": [
                            self._unit_payload(item, True)
                            for item in document.working_gold.alignment_units[index:index + 2]
                        ],
                        "result": self._unit_payload(candidate.alignment_units[index], True),
                    },
                )

    def preview_disposition_edit(
        self, paragraph_id: str, reason: str, note: str | None,
        expected_revision: int,
    ) -> dict[str, Any]:
        with self._lock:
            with ExclusiveFileLock(self._session_lock_path):
                document = self._refresh_active_locked()
                self._require_preview_revision(document, expected_revision)
                payload = _canonical_payload({
                    "paragraph_id": paragraph_id, "reason": reason, "note": _optional(note),
                })
                candidate = self._apply_event(
                    document.working_gold, "edit_disposition", payload,
                )
                edited = next(
                    item for item in candidate.paragraph_dispositions
                    if str(item.paragraph_id) == paragraph_id
                )
                return self._register_preview(
                    "edit_disposition", payload, candidate,
                    {
                        "paragraph_id": paragraph_id, "reason": edited.reason.value,
                        "note": edited.note,
                    },
                )

    def preview_rollback(self, keep_units: int, expected_revision: int) -> dict[str, Any]:
        with self._lock:
            with ExclusiveFileLock(self._session_lock_path):
                document = self._refresh_active_locked()
                self._require_preview_revision(document, expected_revision)
                payload = {"keep_units": keep_units}
                candidate = self._apply_event(
                    document.working_gold, "rollback", payload,
                )
                removed_units = document.working_gold.alignment_units[keep_units:]
                removed_dispositions = tuple(
                    item for item in document.working_gold.paragraph_dispositions
                    if item not in candidate.paragraph_dispositions
                )
                return self._register_preview(
                    "rollback", payload, candidate,
                    {
                        "keep_units": keep_units,
                        "removed_units": [
                            self._unit_payload(item, True) for item in removed_units
                        ],
                        "removed_dispositions": [
                            {
                                "paragraph_id": str(item.paragraph_id),
                                "reason": item.reason.value,
                                "note": item.note,
                                "after_alignment_unit": item.after_alignment_unit,
                            }
                            for item in removed_dispositions
                        ],
                    },
                )
    def confirm_preview(
        self, preview_token: str, request_id: str, expected_revision: int,
        expected_operation: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            record = self._previews.get(preview_token)
            if record is None or record.expires_at < time.monotonic():
                self._previews.pop(preview_token, None)
                raise PairwiseReviewError("Pairwise preview is unknown or expired; preview again")
            if self.session_document is None or record.session_id != self.session_document.session_id:
                raise PairwiseReviewError("Pairwise preview belongs to another session")
            if expected_operation is not None and record.operation != expected_operation:
                raise PairwiseReviewError("Pairwise preview belongs to another operation")
            if expected_revision != record.revision:
                raise PairwiseReviewError("Pairwise preview revision differs; preview again")
            try:
                with ExclusiveFileLock(self._session_lock_path):
                    applied = self._commit_event_locked(
                        record.operation, record.payload, expected_revision, request_id,
                        record.candidate_sha256,
                    )
            except FileLockTimeout as error:
                raise PairwiseReviewError(str(error)) from error
            result = self.snapshot()
            result["idempotent"] = not applied
            return result

    def undo(self, expected_revision: int, request_id: str) -> dict[str, Any]:
        with self._lock:
            try:
                with ExclusiveFileLock(self._session_lock_path):
                    document = self._refresh_active_locked()
                    payload: dict[str, Any] = {}
                    if self._existing_request(document, request_id, "undo", payload):
                        result = self.snapshot()
                        result["idempotent"] = True
                        return result
                    _require_revision(expected_revision)
                    if document.revision != expected_revision:
                        raise PairwiseReviewError(
                            f"Pairwise Review revision conflict: expected {expected_revision}, "
                            f"current {document.revision}"
                        )
                    if not document.journal:
                        raise PairwiseReviewError("There is no Pairwise Review action to undo")
                    journal = document.journal[:-1]
                    provisional = replace(document, journal=journal)
                    replayed = self._replay_journal(document.baseline_gold, journal)
                    now, revision = _now(), document.revision + 1
                    record = PairwiseRequestRecord(
                        request_id, "undo", _action_hash("undo", payload), revision,
                        pairwise_gold_document_sha256(replayed), now,
                    )
                    updated = replace(
                        provisional, revision=revision,
                        request_history=document.request_history + (record,),
                        working_gold=replayed, updated_at=now,
                    )
                    self._replay_and_validate(updated)
                    assert self.session_path is not None
                    save_pairwise_session(self.session_path, updated)
                    self.session_document, self.gold = updated, replayed
                    self.cursors = self._validate_partial(replayed)
            except FileLockTimeout as error:
                raise PairwiseReviewError(str(error)) from error
            result = self.snapshot()
            result["idempotent"] = False
            return result

    def _replay_journal(
        self, baseline: PairwiseGold, journal: tuple[PairwiseReviewEvent, ...],
    ) -> PairwiseGold:
        gold = baseline
        for event in journal:
            if pairwise_gold_document_sha256(gold) != event.before_working_gold_sha256:
                raise PairwiseReviewError("Pairwise Review journal before-hash chain diverged")
            gold = self._apply_event(gold, event.operation, event.payload)
            if pairwise_gold_document_sha256(gold) != event.after_working_gold_sha256:
                raise PairwiseReviewError("Pairwise Review journal after-hash chain diverged")
        return gold

    def use_remaining_bootstrap_span(self) -> dict[str, Any]:
        if not self.bootstrap:
            raise PairwiseReviewError("No trilingual bootstrap is attached")
        next_ids = {
            side: (
                str(chapter.paragraphs[self.cursors[side]].id)
                if self.cursors[side] < len(chapter.paragraphs) else None
            )
            for side, chapter in self.chapter_by_side.items()
        }
        for item in self.bootstrap["items"]:
            if item["kind"] not in {"coarse_region", "gap_suggestion"}:
                continue
            specs: dict[str, Any] = {}
            compatible = True
            for side_name in ("left", "right"):
                value = item[side_name]
                if "gap" in value:
                    specs[side_name] = {"gap": True, **value["gap"]}
                    continue
                ids = value["paragraphs"]
                if next_ids[side_name] not in ids:
                    compatible = False
                    break
                specs[side_name] = {"count": len(ids) - ids.index(next_ids[side_name])}
            if compatible:
                return {
                    "mutated": False, "confirmed": False,
                    "trilingual_unit_id": item["trilingual_unit_id"], "sides": specs,
                }
        raise PairwiseReviewError("Current cursors are not inside one remaining bootstrap region")

    def finish(self) -> dict[str, Any]:
        with self._lock:
            if self.session_path is not None and self.session_path.name.endswith(".active.json"):
                with ExclusiveFileLock(self._session_lock_path):
                    self._refresh_active_locked()
            try:
                validate_pairwise_gold(
                    self.gold, self.left, self.right,
                    self.gold.left_source.normalized_sha256,
                    self.gold.right_source.normalized_sha256,
                    require_complete=True,
                )
            except PairwiseGoldValidationError as error:
                raise PairwiseReviewError(str(error)) from error
            return {"valid": True, "summary": self._summary()}

    def publish(
        self, *, explicit_confirmation: bool, expected_revision: int, request_id: str,
    ) -> dict[str, Any]:
        payload = {"explicit_confirmation": True}
        with self._lock:
            if not explicit_confirmation:
                raise PairwiseReviewError("Pairwise Gold publication requires explicit confirmation")
            _require_request_id(request_id)
            _require_revision(expected_revision)
            try:
                # Global order: session lock, then target Gold lock.
                with ExclusiveFileLock(self._session_lock_path):
                    with ExclusiveFileLock(self._gold_lock_path):
                        retry = self._terminal_retry_locked(request_id, "publish", payload)
                        if retry is not None:
                            return retry
                        document = self._refresh_active_locked()
                        if self._existing_request(document, request_id, "publish", payload):
                            result = self.snapshot()
                            result["idempotent"] = True
                            return result
                        if document.revision != expected_revision:
                            raise PairwiseReviewError(
                                f"Pairwise Review revision conflict: expected {expected_revision}, "
                                f"current {document.revision}"
                            )
                        validate_pairwise_gold(
                            document.working_gold, self.left, self.right,
                            document.working_gold.left_source.normalized_sha256,
                            document.working_gold.right_source.normalized_sha256,
                            require_complete=True,
                        )
                        target_bytes = self.gold_path.read_bytes()
                        if (
                            hashlib.sha256(target_bytes).hexdigest()
                            != document.base_pairwise_gold_file_sha256
                        ):
                            raise PairwiseReviewError(
                                "Pairwise Gold baseline changed; publication rejected and session preserved"
                            )
                        if document.working_gold.status is not PairwiseGoldStatus.DRAFT:
                            raise PairwiseReviewError("Pairwise publication may write draft status only")
                        expected, now = (
                            pairwise_gold_document_sha256(document.working_gold), _now(),
                        )
                        intent = replace(
                            document, publication_request_id=request_id,
                            publication_expected_revision=expected_revision,
                            publication_expected_document_sha256=expected,
                            publication_started_at=now, updated_at=now,
                        )
                        assert self.session_path is not None
                        save_pairwise_session(self.session_path, intent)
                        self.session_document = intent
                        replace_pairwise_gold_atomic(self.gold_path, document.working_gold)
                        written = self.gold_path.read_bytes()
                        if written != pairwise_gold_json_bytes(document.working_gold):
                            raise PairwiseReviewError(
                                "Atomic Pairwise Gold publication verification failed"
                            )
                        self._finalize_published_locked(
                            hashlib.sha256(written).hexdigest(), recovered=False,
                        )
            except (FileLockTimeout, PairwiseGoldValidationError) as error:
                raise PairwiseReviewError(str(error)) from error
            result = self.snapshot()
            result["publication"] = {
                "published": True, "status": "draft", "recovered": False,
                "file_sha256": self.session_document.published_gold_file_sha256,
            }
            result["idempotent"] = False
            return result

    def _finalize_published_locked(self, digest: str, recovered: bool) -> None:
        assert self.session_document is not None and self.session_path is not None
        document = self.session_document
        if not all((
            document.publication_request_id,
            document.publication_expected_document_sha256,
            document.publication_started_at,
        )):
            raise PairwiseReviewError("Pairwise publication recovery intent is incomplete")
        payload = {"explicit_confirmation": True}
        now, revision = _now(), document.revision + 1
        request = PairwiseRequestRecord(
            document.publication_request_id, "publish", _action_hash("publish", payload),
            revision, pairwise_gold_document_sha256(document.working_gold), now,
        )
        final = replace(
            document, status=PairwiseReviewSessionStatus.PUBLISHED,
            revision=revision, request_history=document.request_history + (request,),
            publication_request_id=None, publication_expected_revision=None,
            publication_expected_document_sha256=None, publication_started_at=None,
            published_gold_file_sha256=digest, published_at=now, updated_at=now,
        )
        archive = self.paths.archived_pairwise_review_session(
            self.gold.work_id, self.gold.left_source.source_id,
            self.gold.right_source.source_id, self.gold.chapter,
            final.session_id, final.status.value,
        )
        self.session_path = archive_pairwise_session(self.session_path, archive, final)
        self.session_document = final
        self.gold = pairwise_gold_from_json_bytes(
            self.gold_path.read_bytes(), str(self.gold_path),
        )
        self.cursors = self._validate_partial(self.gold)
        self.publication_recovered = recovered

    def discard(self, expected_revision: int, request_id: str) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        with self._lock:
            _require_request_id(request_id)
            _require_revision(expected_revision)
            try:
                with ExclusiveFileLock(self._session_lock_path):
                    retry = self._terminal_retry_locked(request_id, "discard", payload)
                    if retry is not None:
                        return retry
                    document = self._refresh_active_locked()
                    if self._existing_request(document, request_id, "discard", payload):
                        result = self.snapshot()
                        result["idempotent"] = True
                        return result
                    if document.revision != expected_revision:
                        raise PairwiseReviewError(
                            f"Pairwise Review revision conflict: expected {expected_revision}, "
                            f"current {document.revision}"
                        )
                    now, revision = _now(), document.revision + 1
                    request = PairwiseRequestRecord(
                        request_id, "discard", _action_hash("discard", payload), revision,
                        pairwise_gold_document_sha256(document.working_gold), now,
                    )
                    final = replace(
                        document, status=PairwiseReviewSessionStatus.DISCARDED,
                        revision=revision, request_history=document.request_history + (request,),
                        updated_at=now,
                    )
                    assert self.session_path is not None
                    archive = self.paths.archived_pairwise_review_session(
                        self.gold.work_id, self.gold.left_source.source_id,
                        self.gold.right_source.source_id, self.gold.chapter,
                        final.session_id, final.status.value,
                    )
                    self.session_path = archive_pairwise_session(
                        self.session_path, archive, final,
                    )
                    self.session_document = final
            except FileLockTimeout as error:
                raise PairwiseReviewError(str(error)) from error
            result = self.snapshot()
            result["idempotent"] = False
            return result

    def _side_payload(
        self, side: PairwiseAlignmentSide, include_text: bool,
    ) -> dict[str, Any]:
        if side.gap:
            return {"gap": {"reason": side.gap.reason.value, "note": side.gap.note}}
        result: dict[str, Any] = {"paragraphs": [str(item) for item in side.paragraphs]}
        if include_text:
            lookup = {
                str(item.id): _paragraph_payload(item)
                for chapter in self.chapters for item in chapter.paragraphs
            }
            result["paragraph_text"] = [lookup[str(item)] for item in side.paragraphs]
        return result

    def _unit_payload(
        self, unit: PairwiseAlignmentUnit, include_text: bool = False,
    ) -> dict[str, Any]:
        return {
            "id": unit.id, "left": self._side_payload(unit.left, include_text),
            "right": self._side_payload(unit.right, include_text), "note": unit.note,
        }

    def _summary(self) -> dict[str, Any]:
        return {
            "target_pairwise_gold": str(self.gold_path), "status": self.gold.status.value,
            "alignment_units": len(self.gold.alignment_units),
            "paragraph_dispositions": len(self.gold.paragraph_dispositions),
            "source_hashes_verified": True,
        }

    def snapshot(self) -> dict[str, Any]:
        counts = {
            side: len(chapter.paragraphs)
            for side, chapter in self.chapter_by_side.items()
        }
        session = self.session_document
        return {
            "read_only": self.read_only,
            "work_id": self.gold.work_id, "chapter": self.gold.chapter,
            "direction": self.gold.direction.value, "pair_key": self.gold.pair_key,
            "status": self.gold.status.value,
            "session": None if session is None else {
                "session_id": session.session_id, "status": session.status.value,
                "path": str(self.session_path),
                "schema_version": session.session_schema_version,
                "review_semantics_version": session.review_semantics_version,
                "revision": session.revision,
                "working_gold_sha256": pairwise_gold_document_sha256(self.gold),
                "journal_length": len(session.journal),
            },
            "history": [] if session is None else [
                {
                    "sequence": item.sequence, "operation": item.operation,
                    "created_at": item.created_at,
                }
                for item in session.journal
            ],
            "cursors": dict(self.cursors), "counts": counts,
            "progress": {
                side: {"consumed": self.cursors[side], "total": counts[side]}
                for side in ("left", "right")
            },
            "sources": {
                side: {
                    "source_id": str(chapter.source), "language": chapter.language.value,
                    "paragraphs": [_paragraph_payload(item) for item in chapter.paragraphs],
                }
                for side, chapter in self.chapter_by_side.items()
            },
            "alignment_units": [
                self._unit_payload(item, True) for item in self.gold.alignment_units
            ],
            "paragraph_dispositions": [
                {
                    "paragraph_id": str(item.paragraph_id), "reason": item.reason.value,
                    "note": item.note, "after_alignment_unit": item.after_alignment_unit,
                }
                for item in self.gold.paragraph_dispositions
            ],
            "bootstrap": self.bootstrap,
            "publication_recovered": self.publication_recovered,
        }
