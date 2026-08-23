"""Persistent ignored workspace for Pairwise Gold human decisions."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..gold.pairwise.model import PairwiseGold, PairwiseGoldSource


class PairwiseReviewSessionStatus(str, Enum):
    ACTIVE = "active"
    PUBLISHED = "published"
    DISCARDED = "discarded"


@dataclass(frozen=True)
class PairwiseReviewEvent:
    sequence: int
    request_id: str
    operation: str
    payload: dict[str, Any]
    before_working_gold_sha256: str
    after_working_gold_sha256: str
    created_at: str

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 1:
            raise ValueError("Pairwise Review event sequence must be positive")
        _require_uuid(self.request_id, "Pairwise Review event request_id")
        if not self.operation or not isinstance(self.payload, dict):
            raise ValueError("Pairwise Review event requires operation and canonical payload")
        _require_hash(self.before_working_gold_sha256, "Pairwise Review event before hash")
        _require_hash(self.after_working_gold_sha256, "Pairwise Review event after hash")
        if not self.created_at:
            raise ValueError("Pairwise Review event requires created_at")


@dataclass(frozen=True)
class PairwiseRequestRecord:
    request_id: str
    operation: str
    payload_sha256: str
    resulting_revision: int
    resulting_working_gold_sha256: str
    created_at: str

    def __post_init__(self) -> None:
        _require_uuid(self.request_id, "Pairwise Review request_id")
        if not self.operation:
            raise ValueError("Pairwise Review request record requires operation")
        _require_hash(self.payload_sha256, "Pairwise Review request payload hash")
        _require_hash(self.resulting_working_gold_sha256, "Pairwise Review request result hash")
        if isinstance(self.resulting_revision, bool) or not isinstance(self.resulting_revision, int) or self.resulting_revision < 1:
            raise ValueError("Pairwise Review request resulting revision must be positive")
        if not self.created_at:
            raise ValueError("Pairwise Review request record requires created_at")


def _require_uuid(value: str, context: str) -> None:
    try:
        uuid.UUID(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{context} must be a UUID") from error


def _require_hash(value: str, context: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{context} must be a SHA-256")


@dataclass(frozen=True)
class PairwiseReviewSession:
    session_schema_version: str
    review_semantics_version: str
    session_id: str
    status: PairwiseReviewSessionStatus
    created_with_reviewer: str
    last_opened_with_reviewer: str
    created_at: str
    updated_at: str
    target_pairwise_gold_path: str
    target_pairwise_gold_schema_version: str
    base_pairwise_gold_file_sha256: str
    work_id: str
    chapter: int
    pair_key: str
    source_snapshot: tuple[PairwiseGoldSource, PairwiseGoldSource]
    bootstrap_trilingual_gold_path: str | None
    bootstrap_trilingual_gold_document_sha256: str | None
    baseline_gold: PairwiseGold
    revision: int
    journal: tuple[PairwiseReviewEvent, ...]
    request_history: tuple[PairwiseRequestRecord, ...]
    working_gold: PairwiseGold
    publication_request_id: str | None = None
    publication_expected_revision: int | None = None
    publication_expected_document_sha256: str | None = None
    publication_started_at: str | None = None
    published_gold_file_sha256: str | None = None
    published_at: str | None = None

    def __post_init__(self) -> None:
        _require_uuid(self.session_id, "Pairwise Review Session ID")
        if self.chapter < 1:
            raise ValueError("Invalid Pairwise Review Session chapter")
        _require_hash(self.base_pairwise_gold_file_sha256, "Pairwise Review Session base hash")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("Pairwise Review Session revision must be non-negative")
        for gold in (self.baseline_gold, self.working_gold):
            if gold.work_id != self.work_id or gold.chapter != self.chapter:
                raise ValueError("Pairwise Review Session and Gold coordinates differ")
            if gold.pair_key != self.pair_key:
                raise ValueError("Pairwise Review Session pair identity differs from Gold")
        expected_sources = tuple((item.source_id, item.normalized_sha256) for item in self.source_snapshot)
        for gold in (self.baseline_gold, self.working_gold):
            if expected_sources != tuple(
                (item.source_id, item.normalized_sha256)
                for item in (gold.left_source, gold.right_source)
            ):
                raise ValueError("Pairwise Review Session source snapshot differs from Gold")
        bootstrap = (self.bootstrap_trilingual_gold_path, self.bootstrap_trilingual_gold_document_sha256)
        if any(bootstrap) != all(bootstrap):
            raise ValueError("Pairwise Review bootstrap path and hash must be set together")
        intent = (
            self.publication_request_id, self.publication_expected_revision,
            self.publication_expected_document_sha256, self.publication_started_at,
        )
        if any(value is not None for value in intent) != all(value is not None for value in intent):
            raise ValueError("Pairwise publication intent fields must be set together")
        if self.publication_request_id is not None:
            _require_uuid(self.publication_request_id, "Pairwise publication request_id")
        if self.publication_expected_revision is not None and (
            isinstance(self.publication_expected_revision, bool)
            or not isinstance(self.publication_expected_revision, int)
            or self.publication_expected_revision < 0
        ):
            raise ValueError("Pairwise publication expected revision is invalid")
        for value in (
            self.bootstrap_trilingual_gold_document_sha256,
            self.publication_expected_document_sha256,
            self.published_gold_file_sha256,
        ):
            if value is not None:
                _require_hash(value, "Pairwise Review SHA-256 metadata")
        published = (self.published_gold_file_sha256, self.published_at)
        if any(published) != all(published):
            raise ValueError("Pairwise published hash and time must be set together")
        if self.status is PairwiseReviewSessionStatus.PUBLISHED and not all(published):
            raise ValueError("Published Pairwise Review Session requires publication provenance")
        if tuple(item.sequence for item in self.journal) != tuple(range(1, len(self.journal) + 1)):
            raise ValueError("Pairwise Review event sequences must be contiguous")
        event_requests = [item.request_id for item in self.journal]
        request_ids = [item.request_id for item in self.request_history]
        if len(event_requests) != len(set(event_requests)):
            raise ValueError("Pairwise Review journal request IDs must be unique")
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("Pairwise Review request IDs must be unique")
        if not set(event_requests).issubset(request_ids):
            raise ValueError("Every Pairwise Review event must have a request-history record")
        if tuple(item.resulting_revision for item in self.request_history) != tuple(
            range(1, self.revision + 1)
        ):
            raise ValueError(
                "Pairwise Review request history must account for every committed revision"
            )
