"""Atomic codec and archive boundary for persistent review sessions."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..domain import Language, SourceId
from ..gold.io import gold_from_dict, gold_to_dict
from ..infrastructure.json_io import read_json, write_json_atomic
from .compatibility import migrate_session_payload
from .session_model import ReviewSessionDocument, ReviewSessionStatus, ReviewSourceSnapshot


def session_to_dict(document: ReviewSessionDocument) -> dict[str, Any]:
    return {
        "session_schema_version": document.session_schema_version,
        "review_semantics_version": document.review_semantics_version,
        "session_id": document.session_id,
        "status": document.status.value,
        "created_with_reviewer": document.created_with_reviewer,
        "last_opened_with_reviewer": document.last_opened_with_reviewer,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
        "target_gold_path": document.target_gold_path,
        "target_gold_schema_version": document.target_gold_schema_version,
        "base_gold_sha256": document.base_gold_sha256,
        "work_id": document.work_id,
        "chapter": document.chapter,
        "source_snapshot": [
            {
                "source_id": str(item.source_id),
                "language": item.language.value,
                "normalized_path": item.normalized_path,
                "normalized_sha256": item.normalized_sha256,
                "normalized_schema_version": item.normalized_schema_version,
                "paragraph_count": item.paragraph_count,
            }
            for item in document.source_snapshot
        ],
        "working_gold": gold_to_dict(document.working_gold),
        "publication_expected_gold_sha256": document.publication_expected_gold_sha256,
        "publication_started_at": document.publication_started_at,
        "published_gold_sha256": document.published_gold_sha256,
        "published_at": document.published_at,
    }


def session_from_dict(payload: dict[str, Any]) -> ReviewSessionDocument:
    snapshots = tuple(
        ReviewSourceSnapshot(
            SourceId(str(item["source_id"])), Language(str(item["language"])),
            str(item["normalized_path"]), str(item["normalized_sha256"]),
            str(item["normalized_schema_version"]), int(item["paragraph_count"]),
        )
        for item in payload.get("source_snapshot", [])
    )
    return ReviewSessionDocument(
        session_schema_version=str(payload["session_schema_version"]),
        review_semantics_version=str(payload["review_semantics_version"]),
        session_id=str(payload["session_id"]),
        status=ReviewSessionStatus(str(payload["status"])),
        created_with_reviewer=str(payload["created_with_reviewer"]),
        last_opened_with_reviewer=str(payload["last_opened_with_reviewer"]),
        created_at=str(payload["created_at"]),
        updated_at=str(payload["updated_at"]),
        target_gold_path=str(payload["target_gold_path"]),
        target_gold_schema_version=str(payload["target_gold_schema_version"]),
        base_gold_sha256=str(payload["base_gold_sha256"]),
        work_id=str(payload["work_id"]),
        chapter=int(payload["chapter"]),
        source_snapshot=snapshots,
        working_gold=gold_from_dict(dict(payload["working_gold"])),
        publication_expected_gold_sha256=payload.get("publication_expected_gold_sha256"),
        publication_started_at=payload.get("publication_started_at"),
        published_gold_sha256=payload.get("published_gold_sha256"),
        published_at=payload.get("published_at"),
    )


def save_session(path: Path, document: ReviewSessionDocument) -> None:
    write_json_atomic(path, session_to_dict(document))


def load_session(path: Path) -> ReviewSessionDocument:
    payload = migrate_session_payload(path, read_json(path))
    return session_from_dict(payload)


def archive_session(active_path: Path, archive_path: Path, document: ReviewSessionDocument) -> Path:
    save_session(active_path, document)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(active_path, archive_path)
    return archive_path


def active_session_metadata(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    result: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.active.json")):
        document = load_session(path)
        result.append({
            "session_id": document.session_id,
            "target_gold": document.target_gold_path,
            "work_id": document.work_id,
            "chapter": document.chapter,
            "status": document.status.value,
            "sources": [
                {
                    "source_id": str(item.source_id),
                    "language": item.language.value,
                    "normalized_sha256": item.normalized_sha256,
                }
                for item in document.source_snapshot
            ],
            "updated_at": document.updated_at,
            "path": str(path),
        })
    return result

