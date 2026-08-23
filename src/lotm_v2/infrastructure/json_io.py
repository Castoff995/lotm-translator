"""The only direct JSON filesystem boundary in v2."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any


def decode_json_object(data: bytes, context: str = "JSON document") -> dict[str, Any]:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Invalid UTF-8 JSON in {context}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {context}")
    return payload


def read_json(path: Path) -> dict[str, Any]:
    return decode_json_object(path.read_bytes(), str(path))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON durably and replace the destination in one filesystem operation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
