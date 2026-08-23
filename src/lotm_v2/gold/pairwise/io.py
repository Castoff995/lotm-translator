"""Strict, atomic codec for Pairwise Gold JSON."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ...domain import Language, ParagraphId, SourceId
from ...infrastructure.canonical_json import canonical_json_sha256
from ...infrastructure.json_io import decode_json_object, read_json, write_json_atomic
from ..model import GoldFlag, ParagraphDispositionReason
from .model import (
    PAIRWISE_GOLD_ARTIFACT_TYPE, PAIRWISE_GOLD_SCHEMA_VERSION,
    PairwiseAlignmentSide, PairwiseAlignmentUnit, PairwiseCreationMode,
    PairwiseDirection, PairwiseGap, PairwiseGold, PairwiseGoldProvenance,
    PairwiseGoldSource, PairwiseGoldStatus, PairwiseParagraphDisposition,
)


def _object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{context} must be a JSON object")
    return value


def _strict(value: Any, context: str, required: set[str], optional: set[str] = frozenset()) -> dict[str, Any]:
    payload = _object(value, context)
    missing = required - set(payload)
    unknown = set(payload) - required - optional
    if missing:
        raise ValueError(f"{context} missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise ValueError(f"{context} contains unknown fields: {', '.join(sorted(unknown))}")
    return payload


def _string(value: Any, context: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{context} must be a string")
    return value


def _nullable_string(value: Any, context: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{context} must be a string or null")
    return value


def _integer(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{context} must be an integer")
    return value


def _source_from_dict(value: Any, context: str) -> PairwiseGoldSource:
    item = _strict(value, context, {
        "source_id", "language", "normalized_path", "normalized_sha256",
        "normalized_schema_version", "paragraph_count",
    })
    return PairwiseGoldSource(
        SourceId(_string(item["source_id"], f"{context}.source_id")),
        Language(_string(item["language"], f"{context}.language")),
        _string(item["normalized_path"], f"{context}.normalized_path"),
        _string(item["normalized_sha256"], f"{context}.normalized_sha256"),
        _string(item["normalized_schema_version"], f"{context}.normalized_schema_version"),
        _integer(item["paragraph_count"], f"{context}.paragraph_count"),
    )


def _side_from_dict(value: Any, context: str) -> PairwiseAlignmentSide:
    item = _object(value, context)
    if set(item) == {"paragraphs"}:
        paragraphs = item["paragraphs"]
        if not isinstance(paragraphs, list) or not paragraphs:
            raise ValueError(f"{context}.paragraphs must be a non-empty array")
        return PairwiseAlignmentSide(paragraphs=tuple(
            ParagraphId.parse(_string(value, f"{context}.paragraphs[]")) for value in paragraphs
        ))
    if set(item) == {"gap"}:
        gap = _strict(item["gap"], f"{context}.gap", {"reason", "note"})
        return PairwiseAlignmentSide(gap=PairwiseGap(
            GoldFlag(_string(gap["reason"], f"{context}.gap.reason")),
            _nullable_string(gap["note"], f"{context}.gap.note"),
        ))
    raise ValueError(f"{context} must contain exactly paragraphs or gap")


def pairwise_gold_from_dict(value: dict[str, Any]) -> PairwiseGold:
    payload = _strict(value, "pairwise_gold", {
        "schema_version", "artifact_type", "status", "work_id", "chapter", "direction",
        "left_source", "right_source", "alignment_units", "paragraph_dispositions",
        "provenance", "notes",
    })
    raw_units = payload["alignment_units"]
    raw_dispositions = payload["paragraph_dispositions"]
    if not isinstance(raw_units, list) or not isinstance(raw_dispositions, list):
        raise ValueError("Pairwise Gold units and dispositions must be arrays")
    units: list[PairwiseAlignmentUnit] = []
    for index, raw in enumerate(raw_units):
        item = _strict(raw, f"alignment_units[{index}]", {"id", "left", "right", "note"})
        units.append(PairwiseAlignmentUnit(
            _string(item["id"], f"alignment_units[{index}].id"),
            _side_from_dict(item["left"], f"alignment_units[{index}].left"),
            _side_from_dict(item["right"], f"alignment_units[{index}].right"),
            _nullable_string(item["note"], f"alignment_units[{index}].note"),
        ))
    dispositions: list[PairwiseParagraphDisposition] = []
    for index, raw in enumerate(raw_dispositions):
        item = _strict(raw, f"paragraph_dispositions[{index}]", {
            "paragraph_id", "reason", "note", "after_alignment_unit",
        })
        dispositions.append(PairwiseParagraphDisposition(
            ParagraphId.parse(_string(item["paragraph_id"], f"paragraph_dispositions[{index}].paragraph_id")),
            ParagraphDispositionReason(_string(item["reason"], f"paragraph_dispositions[{index}].reason")),
            _nullable_string(item["note"], f"paragraph_dispositions[{index}].note"),
            _integer(item["after_alignment_unit"], f"paragraph_dispositions[{index}].after_alignment_unit"),
        ))
    raw_provenance = _strict(payload["provenance"], "provenance", {
        "creation_mode", "bootstrap_trilingual_gold_path",
        "bootstrap_trilingual_gold_document_sha256",
    })
    return PairwiseGold(
        _string(payload["schema_version"], "schema_version"),
        _string(payload["artifact_type"], "artifact_type"),
        PairwiseGoldStatus(_string(payload["status"], "status")),
        _string(payload["work_id"], "work_id"), _integer(payload["chapter"], "chapter"),
        PairwiseDirection(_string(payload["direction"], "direction")),
        _source_from_dict(payload["left_source"], "left_source"),
        _source_from_dict(payload["right_source"], "right_source"), tuple(units),
        tuple(dispositions), PairwiseGoldProvenance(
            PairwiseCreationMode(_string(raw_provenance["creation_mode"], "provenance.creation_mode")),
            _nullable_string(raw_provenance["bootstrap_trilingual_gold_path"], "provenance.bootstrap_trilingual_gold_path"),
            _nullable_string(raw_provenance["bootstrap_trilingual_gold_document_sha256"], "provenance.bootstrap_trilingual_gold_document_sha256"),
        ), _nullable_string(payload["notes"], "notes"),
    )


def _source_to_dict(source: PairwiseGoldSource) -> dict[str, Any]:
    return {
        "source_id": str(source.source_id), "language": source.language.value,
        "normalized_path": source.normalized_path,
        "normalized_sha256": source.normalized_sha256,
        "normalized_schema_version": source.normalized_schema_version,
        "paragraph_count": source.paragraph_count,
    }


def _side_to_dict(side: PairwiseAlignmentSide) -> dict[str, Any]:
    if side.gap:
        return {"gap": {"reason": side.gap.reason.value, "note": side.gap.note}}
    return {"paragraphs": [str(item) for item in side.paragraphs]}


def pairwise_gold_to_dict(gold: PairwiseGold) -> dict[str, Any]:
    return {
        "schema_version": gold.schema_version,
        "artifact_type": gold.artifact_type,
        "status": gold.status.value,
        "work_id": gold.work_id,
        "chapter": gold.chapter,
        "direction": gold.direction.value,
        "left_source": _source_to_dict(gold.left_source),
        "right_source": _source_to_dict(gold.right_source),
        "alignment_units": [
            {"id": unit.id, "left": _side_to_dict(unit.left),
             "right": _side_to_dict(unit.right), "note": unit.note}
            for unit in gold.alignment_units
        ],
        "paragraph_dispositions": [
            {"paragraph_id": str(item.paragraph_id), "reason": item.reason.value,
             "note": item.note, "after_alignment_unit": item.after_alignment_unit}
            for item in gold.paragraph_dispositions
        ],
        "provenance": {
            "creation_mode": gold.provenance.creation_mode.value,
            "bootstrap_trilingual_gold_path": gold.provenance.bootstrap_trilingual_gold_path,
            "bootstrap_trilingual_gold_document_sha256": (
                gold.provenance.bootstrap_trilingual_gold_document_sha256
            ),
        },
        "notes": gold.notes,
    }


def pairwise_gold_json_bytes(gold: PairwiseGold) -> bytes:
    return (json.dumps(pairwise_gold_to_dict(gold), ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def pairwise_gold_document_sha256(gold: PairwiseGold) -> str:
    return canonical_json_sha256(pairwise_gold_to_dict(gold))


def pairwise_gold_file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_pairwise_gold(path: Path) -> PairwiseGold:
    value = pairwise_gold_from_dict(read_json(path))
    if value.schema_version != PAIRWISE_GOLD_SCHEMA_VERSION or value.artifact_type != PAIRWISE_GOLD_ARTIFACT_TYPE:
        raise ValueError("Not a supported Pairwise Gold document")
    return value


def pairwise_gold_from_json_bytes(data: bytes, context: str = "Pairwise Gold") -> PairwiseGold:
    value = pairwise_gold_from_dict(decode_json_object(data, context))
    if value.schema_version != PAIRWISE_GOLD_SCHEMA_VERSION or value.artifact_type != PAIRWISE_GOLD_ARTIFACT_TYPE:
        raise ValueError("Not a supported Pairwise Gold document")
    return value


def save_pairwise_gold(path: Path, gold: PairwiseGold, *, allow_identical: bool = False) -> None:
    expected = pairwise_gold_json_bytes(gold)
    if path.exists():
        if allow_identical and path.read_bytes() == expected:
            return
        raise FileExistsError(f"Pairwise Gold output already exists: {path}")
    write_json_atomic(path, pairwise_gold_to_dict(gold))


def replace_pairwise_gold_atomic(path: Path, gold: PairwiseGold) -> None:
    write_json_atomic(path, pairwise_gold_to_dict(gold))
