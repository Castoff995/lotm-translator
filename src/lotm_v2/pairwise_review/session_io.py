"""Strict atomic codec and crash-safe archive boundary for Pairwise Review Sessions."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..domain import Language, SourceId
from ..gold.pairwise.io import pairwise_gold_from_dict, pairwise_gold_to_dict
from ..gold.pairwise.model import PairwiseGoldSource
from ..infrastructure.json_io import read_json, write_json_atomic
from .compatibility import PAIRWISE_REVIEW_SESSION_SCHEMA_VERSION
from .session_model import (
    PairwiseRequestRecord, PairwiseReviewEvent, PairwiseReviewSession,
    PairwiseReviewSessionStatus,
)


LEGACY_PAIRWISE_SESSION_MESSAGE = (
    "legacy Pairwise Review Session requires explicit discard/archive or migration decision"
)


class LegacyPairwiseReviewSessionError(ValueError):
    pass


def _string(value: Any, context: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{context} must be a string")
    return value


def _nullable_string(value: Any, context: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{context} must be a string or null")
    return value


def _nullable_integer(value: Any, context: str) -> int | None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
        raise ValueError(f"{context} must be an integer or null")
    return value


def _integer(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{context} must be an integer")
    return value


def _object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{context} must be an object")
    return value


def _source_payload(item: Any, context: str) -> dict[str, Any]:
    payload = _object(item, context)
    expected = {
        "source_id", "language", "normalized_path", "normalized_sha256",
        "normalized_schema_version", "paragraph_count",
    }
    if set(payload) != expected:
        raise ValueError(f"{context} must contain exactly the Pairwise source snapshot fields")
    return payload


def _source_to_dict(item: PairwiseGoldSource) -> dict[str, Any]:
    return {
        "source_id": str(item.source_id), "language": item.language.value,
        "normalized_path": item.normalized_path,
        "normalized_sha256": item.normalized_sha256,
        "normalized_schema_version": item.normalized_schema_version,
        "paragraph_count": item.paragraph_count,
    }


def _source_from_dict(item: dict[str, Any]) -> PairwiseGoldSource:
    return PairwiseGoldSource(
        SourceId(_string(item["source_id"], "source_id")),
        Language(_string(item["language"], "language")),
        _string(item["normalized_path"], "normalized_path"),
        _string(item["normalized_sha256"], "normalized_sha256"),
        _string(item["normalized_schema_version"], "normalized_schema_version"),
        _integer(item["paragraph_count"], "paragraph_count"),
    )


def _event_to_dict(item: PairwiseReviewEvent) -> dict[str, Any]:
    return {
        "sequence": item.sequence, "request_id": item.request_id,
        "operation": item.operation, "payload": item.payload,
        "before_working_gold_sha256": item.before_working_gold_sha256,
        "after_working_gold_sha256": item.after_working_gold_sha256,
        "created_at": item.created_at,
    }


def _event_from_dict(value: Any, index: int) -> PairwiseReviewEvent:
    item = _object(value, f"journal[{index}]")
    expected = {
        "sequence", "request_id", "operation", "payload",
        "before_working_gold_sha256", "after_working_gold_sha256", "created_at",
    }
    if set(item) != expected:
        raise ValueError(f"journal[{index}] fields are invalid")
    return PairwiseReviewEvent(
        _integer(item["sequence"], f"journal[{index}].sequence"),
        _string(item["request_id"], f"journal[{index}].request_id"),
        _string(item["operation"], f"journal[{index}].operation"),
        _object(item["payload"], f"journal[{index}].payload"),
        _string(item["before_working_gold_sha256"], f"journal[{index}].before hash"),
        _string(item["after_working_gold_sha256"], f"journal[{index}].after hash"),
        _string(item["created_at"], f"journal[{index}].created_at"),
    )


def _request_to_dict(item: PairwiseRequestRecord) -> dict[str, Any]:
    return {
        "request_id": item.request_id, "operation": item.operation,
        "payload_sha256": item.payload_sha256,
        "resulting_revision": item.resulting_revision,
        "resulting_working_gold_sha256": item.resulting_working_gold_sha256,
        "created_at": item.created_at,
    }


def _request_from_dict(value: Any, index: int) -> PairwiseRequestRecord:
    item = _object(value, f"request_history[{index}]")
    expected = {
        "request_id", "operation", "payload_sha256", "resulting_revision",
        "resulting_working_gold_sha256", "created_at",
    }
    if set(item) != expected:
        raise ValueError(f"request_history[{index}] fields are invalid")
    return PairwiseRequestRecord(
        _string(item["request_id"], f"request_history[{index}].request_id"),
        _string(item["operation"], f"request_history[{index}].operation"),
        _string(item["payload_sha256"], f"request_history[{index}].payload_sha256"),
        _integer(item["resulting_revision"], f"request_history[{index}].resulting_revision"),
        _string(item["resulting_working_gold_sha256"], f"request_history[{index}].result hash"),
        _string(item["created_at"], f"request_history[{index}].created_at"),
    )


def pairwise_session_to_dict(document: PairwiseReviewSession) -> dict[str, Any]:
    return {
        "session_schema_version": document.session_schema_version,
        "review_semantics_version": document.review_semantics_version,
        "session_id": document.session_id, "status": document.status.value,
        "created_with_reviewer": document.created_with_reviewer,
        "last_opened_with_reviewer": document.last_opened_with_reviewer,
        "created_at": document.created_at, "updated_at": document.updated_at,
        "target_pairwise_gold_path": document.target_pairwise_gold_path,
        "target_pairwise_gold_schema_version": document.target_pairwise_gold_schema_version,
        "base_pairwise_gold_file_sha256": document.base_pairwise_gold_file_sha256,
        "work_id": document.work_id, "chapter": document.chapter, "pair_key": document.pair_key,
        "source_snapshot": [_source_to_dict(item) for item in document.source_snapshot],
        "bootstrap_trilingual_gold_path": document.bootstrap_trilingual_gold_path,
        "bootstrap_trilingual_gold_document_sha256": document.bootstrap_trilingual_gold_document_sha256,
        "baseline_gold": pairwise_gold_to_dict(document.baseline_gold),
        "revision": document.revision,
        "journal": [_event_to_dict(item) for item in document.journal],
        "request_history": [_request_to_dict(item) for item in document.request_history],
        "working_gold": pairwise_gold_to_dict(document.working_gold),
        "publication_request_id": document.publication_request_id,
        "publication_expected_revision": document.publication_expected_revision,
        "publication_expected_document_sha256": document.publication_expected_document_sha256,
        "publication_started_at": document.publication_started_at,
        "published_gold_file_sha256": document.published_gold_file_sha256,
        "published_at": document.published_at,
    }


def pairwise_session_from_dict(payload: dict[str, Any]) -> PairwiseReviewSession:
    schema = payload.get("session_schema_version")
    if schema != PAIRWISE_REVIEW_SESSION_SCHEMA_VERSION:
        if schema == "1.0-draft":
            raise LegacyPairwiseReviewSessionError(LEGACY_PAIRWISE_SESSION_MESSAGE)
        raise ValueError(f"Unsupported Pairwise Review Session schema: {schema!r}")
    expected = {
        "session_schema_version", "review_semantics_version", "session_id", "status",
        "created_with_reviewer", "last_opened_with_reviewer", "created_at", "updated_at",
        "target_pairwise_gold_path", "target_pairwise_gold_schema_version",
        "base_pairwise_gold_file_sha256", "work_id", "chapter", "pair_key",
        "source_snapshot", "bootstrap_trilingual_gold_path",
        "bootstrap_trilingual_gold_document_sha256", "baseline_gold", "revision",
        "journal", "request_history", "working_gold", "publication_request_id",
        "publication_expected_revision", "publication_expected_document_sha256",
        "publication_started_at", "published_gold_file_sha256", "published_at",
    }
    missing, unknown = expected - set(payload), set(payload) - expected
    if missing or unknown:
        raise ValueError(
            f"Invalid Pairwise Review Session fields; missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    sources = payload["source_snapshot"]
    journal, requests = payload["journal"], payload["request_history"]
    if not isinstance(sources, list) or len(sources) != 2:
        raise ValueError("Pairwise Review Session requires exactly two source snapshots")
    if not isinstance(journal, list) or not isinstance(requests, list):
        raise ValueError("Pairwise Review Session journal and request_history must be arrays")
    return PairwiseReviewSession(
        session_schema_version=_string(payload["session_schema_version"], "session_schema_version"),
        review_semantics_version=_string(payload["review_semantics_version"], "review_semantics_version"),
        session_id=_string(payload["session_id"], "session_id"),
        status=PairwiseReviewSessionStatus(_string(payload["status"], "status")),
        created_with_reviewer=_string(payload["created_with_reviewer"], "created_with_reviewer"),
        last_opened_with_reviewer=_string(payload["last_opened_with_reviewer"], "last_opened_with_reviewer"),
        created_at=_string(payload["created_at"], "created_at"),
        updated_at=_string(payload["updated_at"], "updated_at"),
        target_pairwise_gold_path=_string(payload["target_pairwise_gold_path"], "target_pairwise_gold_path"),
        target_pairwise_gold_schema_version=_string(payload["target_pairwise_gold_schema_version"], "target_pairwise_gold_schema_version"),
        base_pairwise_gold_file_sha256=_string(payload["base_pairwise_gold_file_sha256"], "base_pairwise_gold_file_sha256"),
        work_id=_string(payload["work_id"], "work_id"),
        chapter=_integer(payload["chapter"], "chapter"),
        pair_key=_string(payload["pair_key"], "pair_key"),
        source_snapshot=tuple(
            _source_from_dict(_source_payload(item, f"source_snapshot[{index}]"))
            for index, item in enumerate(sources)
        ),
        bootstrap_trilingual_gold_path=_nullable_string(payload["bootstrap_trilingual_gold_path"], "bootstrap_trilingual_gold_path"),
        bootstrap_trilingual_gold_document_sha256=_nullable_string(payload["bootstrap_trilingual_gold_document_sha256"], "bootstrap_trilingual_gold_document_sha256"),
        baseline_gold=pairwise_gold_from_dict(_object(payload["baseline_gold"], "baseline_gold")),
        revision=_integer(payload["revision"], "revision"),
        journal=tuple(_event_from_dict(item, index) for index, item in enumerate(journal)),
        request_history=tuple(_request_from_dict(item, index) for index, item in enumerate(requests)),
        working_gold=pairwise_gold_from_dict(_object(payload["working_gold"], "working_gold")),
        publication_request_id=_nullable_string(payload["publication_request_id"], "publication_request_id"),
        publication_expected_revision=_nullable_integer(payload["publication_expected_revision"], "publication_expected_revision"),
        publication_expected_document_sha256=_nullable_string(payload["publication_expected_document_sha256"], "publication_expected_document_sha256"),
        publication_started_at=_nullable_string(payload["publication_started_at"], "publication_started_at"),
        published_gold_file_sha256=_nullable_string(payload["published_gold_file_sha256"], "published_gold_file_sha256"),
        published_at=_nullable_string(payload["published_at"], "published_at"),
    )


def save_pairwise_session(path: Path, document: PairwiseReviewSession) -> None:
    write_json_atomic(path, pairwise_session_to_dict(document))


def load_pairwise_session(path: Path) -> PairwiseReviewSession:
    return pairwise_session_from_dict(read_json(path))


def archive_pairwise_session(
    active_path: Path, archive_path: Path, document: PairwiseReviewSession,
) -> Path:
    """Write/verify terminal archive first, then remove active state."""
    if archive_path.exists():
        existing = load_pairwise_session(archive_path)
        if existing != document:
            raise ValueError(f"Conflicting Pairwise terminal archive already exists: {archive_path}")
    else:
        write_json_atomic(archive_path, pairwise_session_to_dict(document))
    if load_pairwise_session(archive_path) != document:
        raise ValueError("Pairwise terminal archive verification failed")
    active_path.unlink(missing_ok=True)
    return archive_path


def pairwise_session_metadata(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    return {
        "session_kind": "pairwise",
        "session_id": payload.get("session_id"),
        "target_gold": payload.get("target_pairwise_gold_path"),
        "work_id": payload.get("work_id"), "chapter": payload.get("chapter"),
        "pair_key": payload.get("pair_key"), "status": payload.get("status"),
        "session_schema_version": payload.get("session_schema_version"),
        "review_semantics_version": payload.get("review_semantics_version"),
        "legacy_requires_explicit_decision": payload.get("session_schema_version") == "1.0-draft",
        "updated_at": payload.get("updated_at"), "path": str(path),
    }


def active_pairwise_session_metadata(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    return [pairwise_session_metadata(path) for path in sorted(root.rglob("*.active.json"))]
