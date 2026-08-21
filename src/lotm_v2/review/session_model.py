"""Typed persistent human-review workspace document."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from enum import Enum

from ..domain import Language, SourceId
from ..gold.model import GoldChapter


class ReviewSessionStatus(str, Enum):
    ACTIVE = "active"
    PUBLISHED = "published"
    DISCARDED = "discarded"


@dataclass(frozen=True)
class ReviewSourceSnapshot:
    source_id: SourceId
    language: Language
    normalized_path: str
    normalized_sha256: str
    normalized_schema_version: str
    paragraph_count: int

    def __post_init__(self) -> None:
        if not self.normalized_path or not re.fullmatch(r"[0-9a-f]{64}", self.normalized_sha256):
            raise ValueError("Review source snapshot requires a path and SHA-256")
        if not self.normalized_schema_version or self.paragraph_count < 0:
            raise ValueError("Review source snapshot has invalid normalized metadata")


@dataclass(frozen=True)
class ReviewSessionDocument:
    session_schema_version: str
    review_semantics_version: str
    session_id: str
    status: ReviewSessionStatus
    created_with_reviewer: str
    last_opened_with_reviewer: str
    created_at: str
    updated_at: str
    target_gold_path: str
    target_gold_schema_version: str
    base_gold_sha256: str
    work_id: str
    chapter: int
    source_snapshot: tuple[ReviewSourceSnapshot, ...]
    working_gold: GoldChapter
    publication_expected_gold_sha256: str | None = None
    publication_started_at: str | None = None
    published_gold_sha256: str | None = None
    published_at: str | None = None

    def __post_init__(self) -> None:
        try:
            uuid.UUID(self.session_id)
        except ValueError as error:
            raise ValueError("Review session ID must be a UUID") from error
        if not all((self.created_with_reviewer, self.last_opened_with_reviewer, self.created_at,
                    self.updated_at, self.target_gold_path, self.target_gold_schema_version, self.work_id)):
            raise ValueError("Review session metadata must not be empty")
        if self.chapter < 1 or not re.fullmatch(r"[0-9a-f]{64}", self.base_gold_sha256):
            raise ValueError("Review session has invalid chapter or base Gold hash")
        if self.working_gold.chapter.work_id != self.work_id or self.working_gold.chapter.number != self.chapter:
            raise ValueError("Review session and working Gold coordinates differ")
        if self.working_gold.schema_version != self.target_gold_schema_version:
            raise ValueError("Review session target and working Gold schema versions differ")
        source_ids = [str(item.source_id) for item in self.source_snapshot]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("Review session source snapshot IDs must be unique")
        intent = (self.publication_expected_gold_sha256, self.publication_started_at)
        if any(intent) and not all(intent):
            raise ValueError("Publication intent hash and timestamp must be set together")
        if self.publication_expected_gold_sha256 and not re.fullmatch(
            r"[0-9a-f]{64}", self.publication_expected_gold_sha256,
        ):
            raise ValueError("Invalid expected publication hash")
        published = (self.published_gold_sha256, self.published_at)
        if any(published) and not all(published):
            raise ValueError("Published Gold hash and timestamp must be set together")
        if self.published_gold_sha256 and not re.fullmatch(r"[0-9a-f]{64}", self.published_gold_sha256):
            raise ValueError("Invalid published Gold hash")
        if self.status is ReviewSessionStatus.PUBLISHED and not all(published):
            raise ValueError("Published review session requires publication provenance")
        if self.status is ReviewSessionStatus.ACTIVE and any(published):
            raise ValueError("Active review session cannot already be published")

