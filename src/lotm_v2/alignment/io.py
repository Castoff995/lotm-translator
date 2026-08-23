"""Strict JSON codec for pairwise alignment proposal artifacts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..domain import Language, ParagraphId, SourceId
from ..infrastructure.canonical_json import canonical_json_sha256
from ..infrastructure.json_io import decode_json_object, read_json, write_json
from .model import (
    AlignmentDirection, CoverageMode, PairwiseAlignmentProposal, ProducerProvenance,
    ProposalScore, ProposalSide, ProposalSourceSnapshot, ProposalUnit,
    parameters_sha256,
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
        raise ValueError(f"{context} is missing required fields: {', '.join(sorted(missing))}")
    if unknown:
        raise ValueError(f"{context} contains unknown fields: {', '.join(sorted(unknown))}")
    return payload


def _string(value: Any, context: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{context} must be a string")
    return value


def _integer(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{context} must be an integer")
    return value


def _source_from_dict(value: Any, context: str) -> ProposalSourceSnapshot:
    payload = _strict(value, context, {
        "source_id", "language", "normalized_path", "normalized_sha256",
        "normalized_schema_version", "paragraph_count",
    })
    return ProposalSourceSnapshot(
        source_id=SourceId(_string(payload["source_id"], f"{context}.source_id")),
        language=Language(_string(payload["language"], f"{context}.language")),
        normalized_path=_string(payload["normalized_path"], f"{context}.normalized_path"),
        normalized_sha256=_string(payload["normalized_sha256"], f"{context}.normalized_sha256"),
        normalized_schema_version=_string(
            payload["normalized_schema_version"], f"{context}.normalized_schema_version",
        ),
        paragraph_count=_integer(payload["paragraph_count"], f"{context}.paragraph_count"),
    )


def _producer_from_dict(value: Any) -> ProducerProvenance:
    payload = _strict(
        value, "producer", {"producer_id", "producer_version", "parameters", "parameters_sha256", "code_revision"},
    )
    parameters = _object(payload["parameters"], "producer.parameters")
    code_revision = payload["code_revision"]
    if code_revision is not None:
        code_revision = _string(code_revision, "producer.code_revision")
    return ProducerProvenance(
        producer_id=_string(payload["producer_id"], "producer.producer_id"),
        producer_version=_string(payload["producer_version"], "producer.producer_version"),
        parameters=dict(parameters),
        parameters_sha256=_string(payload["parameters_sha256"], "producer.parameters_sha256"),
        code_revision=code_revision,
    )


def _side_from_dict(value: Any, context: str) -> ProposalSide:
    payload = _object(value, context)
    keys = set(payload)
    if keys == {"paragraphs"}:
        raw = payload["paragraphs"]
        if not isinstance(raw, list) or not raw:
            raise ValueError(f"{context}.paragraphs must be a non-empty array")
        return ProposalSide(paragraphs=tuple(
            ParagraphId.parse(_string(item, f"{context}.paragraphs[]")) for item in raw
        ))
    if keys == {"unmatched"}:
        if payload["unmatched"] is not True:
            raise ValueError(f"{context}.unmatched must be true")
        return ProposalSide(unmatched=True)
    raise ValueError(f"{context} must contain exactly paragraphs or unmatched=true")


def _score_from_dict(value: Any, context: str) -> ProposalScore:
    payload = _strict(value, context, {"name", "value", "semantics"})
    raw_value = payload["value"]
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        raise ValueError(f"{context}.value must be numeric")
    return ProposalScore(
        _string(payload["name"], f"{context}.name"), float(raw_value),
        _string(payload["semantics"], f"{context}.semantics"),
    )


def proposal_from_dict(value: dict[str, Any]) -> PairwiseAlignmentProposal:
    payload = _strict(value, "proposal", {
        "schema_version", "artifact_type", "work_id", "chapter", "direction",
        "coverage_mode", "left_source", "right_source", "producer", "units",
    })
    raw_units = payload["units"]
    if not isinstance(raw_units, list):
        raise ValueError("proposal.units must be an array")
    units: list[ProposalUnit] = []
    for index, raw_unit in enumerate(raw_units):
        unit_payload = _strict(raw_unit, f"units[{index}]", {"id", "left", "right"}, {"scores"})
        raw_scores = unit_payload.get("scores", [])
        if not isinstance(raw_scores, list):
            raise ValueError(f"units[{index}].scores must be an array")
        units.append(ProposalUnit(
            id=_string(unit_payload["id"], f"units[{index}].id"),
            left=_side_from_dict(unit_payload["left"], f"units[{index}].left"),
            right=_side_from_dict(unit_payload["right"], f"units[{index}].right"),
            scores=tuple(
                _score_from_dict(item, f"units[{index}].scores[{score_index}]")
                for score_index, item in enumerate(raw_scores)
            ),
        ))
    return PairwiseAlignmentProposal(
        schema_version=_string(payload["schema_version"], "proposal.schema_version"),
        artifact_type=_string(payload["artifact_type"], "proposal.artifact_type"),
        work_id=_string(payload["work_id"], "proposal.work_id"),
        chapter=_integer(payload["chapter"], "proposal.chapter"),
        direction=AlignmentDirection(_string(payload["direction"], "proposal.direction")),
        coverage_mode=CoverageMode(_string(payload["coverage_mode"], "proposal.coverage_mode")),
        left_source=_source_from_dict(payload["left_source"], "left_source"),
        right_source=_source_from_dict(payload["right_source"], "right_source"),
        producer=_producer_from_dict(payload["producer"]),
        units=tuple(units),
    )


def _source_to_dict(source: ProposalSourceSnapshot) -> dict[str, Any]:
    return {
        "source_id": str(source.source_id), "language": source.language.value,
        "normalized_path": source.normalized_path, "normalized_sha256": source.normalized_sha256,
        "normalized_schema_version": source.normalized_schema_version,
        "paragraph_count": source.paragraph_count,
    }


def _side_to_dict(side: ProposalSide) -> dict[str, Any]:
    return {"unmatched": True} if side.unmatched else {"paragraphs": [str(item) for item in side.paragraphs]}


def proposal_to_dict(proposal: PairwiseAlignmentProposal) -> dict[str, Any]:
    if proposal.producer.parameters_sha256 != parameters_sha256(proposal.producer.parameters):
        raise ValueError("Producer parameters changed after provenance hash was established")
    return {
        "schema_version": proposal.schema_version,
        "artifact_type": proposal.artifact_type,
        "work_id": proposal.work_id,
        "chapter": proposal.chapter,
        "direction": proposal.direction.value,
        "coverage_mode": proposal.coverage_mode.value,
        "left_source": _source_to_dict(proposal.left_source),
        "right_source": _source_to_dict(proposal.right_source),
        "producer": {
            "producer_id": proposal.producer.producer_id,
            "producer_version": proposal.producer.producer_version,
            "parameters": proposal.producer.parameters,
            "parameters_sha256": proposal.producer.parameters_sha256,
            "code_revision": proposal.producer.code_revision,
        },
        "units": [
            {
                "id": unit.id, "left": _side_to_dict(unit.left), "right": _side_to_dict(unit.right),
                **({"scores": [
                    {"name": score.name, "value": score.value, "semantics": score.semantics}
                    for score in unit.scores
                ]} if unit.scores else {}),
            }
            for unit in proposal.units
        ],
    }


def proposal_json_bytes(proposal: PairwiseAlignmentProposal) -> bytes:
    return (json.dumps(proposal_to_dict(proposal), ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def proposal_document_sha256(proposal: PairwiseAlignmentProposal) -> str:
    """Canonical semantic identity, independent of insignificant JSON formatting."""
    return canonical_json_sha256(proposal_to_dict(proposal))


def proposal_file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_proposal(path: Path) -> PairwiseAlignmentProposal:
    return proposal_from_dict(read_json(path))


def proposal_from_json_bytes(
    data: bytes, context: str = "Pairwise Alignment Proposal",
) -> PairwiseAlignmentProposal:
    return proposal_from_dict(decode_json_object(data, context))


def save_proposal(path: Path, proposal: PairwiseAlignmentProposal) -> None:
    write_json(path, proposal_to_dict(proposal))
