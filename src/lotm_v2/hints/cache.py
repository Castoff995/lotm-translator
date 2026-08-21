"""Disposable version-aware JSON caches for local hint computations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..infrastructure.json_io import read_json, write_json_atomic


class HintCache:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    @staticmethod
    def key(kind: str, provider_id: str, payload: dict[str, Any]) -> str:
        canonical = json.dumps(
            {"kind": kind, "provider_id": provider_id, "payload": payload},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def get(self, kind: str, key: str) -> dict[str, Any] | None:
        path = self.root / kind / f"{key}.json"
        return read_json(path) if path.is_file() else None

    def put(self, kind: str, key: str, payload: dict[str, Any]) -> None:
        write_json_atomic(self.root / kind / f"{key}.json", payload)
