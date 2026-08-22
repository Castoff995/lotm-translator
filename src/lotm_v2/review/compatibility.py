"""Central compatibility declaration and explicit session migration registry."""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from .. import GOLD_SCHEMA_VERSION
from ..infrastructure.json_io import write_json_atomic

if TYPE_CHECKING:
    from .session_model import ReviewSessionDocument


REVIEW_APP_VERSION = "1.1.0"
SESSION_SCHEMA_VERSION = "1.0-draft"
REVIEW_SEMANTICS_VERSION = "1.0"

SUPPORTED_SESSION_SCHEMA_VERSIONS = frozenset({SESSION_SCHEMA_VERSION})
SUPPORTED_REVIEW_SEMANTICS_VERSIONS = frozenset({REVIEW_SEMANTICS_VERSION})
SUPPORTED_GOLD_SCHEMA_VERSIONS = frozenset({GOLD_SCHEMA_VERSION})


class ReviewCompatibilityError(ValueError):
    """A persisted review contract is unsafe to resume writable."""


SessionMigrator = Callable[[dict[str, Any]], dict[str, Any]]
SESSION_MIGRATORS: dict[tuple[str, str], SessionMigrator] = {}


def ensure_supported(document: "ReviewSessionDocument") -> None:
    if document.session_schema_version not in SUPPORTED_SESSION_SCHEMA_VERSIONS:
        raise ReviewCompatibilityError(
            f"Unsupported review session schema {document.session_schema_version!r}; "
            "writable resume is blocked until an explicit migration is registered"
        )
    if document.review_semantics_version not in SUPPORTED_REVIEW_SEMANTICS_VERSIONS:
        raise ReviewCompatibilityError(
            f"Unsupported review semantics {document.review_semantics_version!r}; "
            "human decisions cannot be reinterpreted automatically"
        )
    if document.target_gold_schema_version not in SUPPORTED_GOLD_SCHEMA_VERSIONS:
        raise ReviewCompatibilityError(
            f"Unsupported target Gold schema {document.target_gold_schema_version!r}"
        )
    if document.working_gold.schema_version not in SUPPORTED_GOLD_SCHEMA_VERSIONS:
        raise ReviewCompatibilityError(
            f"Unsupported working Gold schema {document.working_gold.schema_version!r}"
        )


def migrate_session_payload(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Apply only a declared deterministic schema migration, preserving a backup."""
    current = str(payload.get("session_schema_version", ""))
    if current in SUPPORTED_SESSION_SCHEMA_VERSIONS:
        return payload
    key = (current, SESSION_SCHEMA_VERSION)
    migrator = SESSION_MIGRATORS.get(key)
    if migrator is None:
        raise ReviewCompatibilityError(
            f"Unsupported review session schema {current!r}; no migration to "
            f"{SESSION_SCHEMA_VERSION!r} is registered"
        )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.backup.{stamp}")
    shutil.copy2(path, backup)
    migrated = migrator(dict(payload))
    if str(migrated.get("session_schema_version")) != SESSION_SCHEMA_VERSION:
        raise ReviewCompatibilityError("Session migrator did not produce the declared target schema")
    write_json_atomic(path, migrated)
    return migrated

