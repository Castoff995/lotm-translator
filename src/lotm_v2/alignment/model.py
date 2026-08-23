"""Phase 3 machine pairwise-alignment proposal contract.

These types are deliberately independent from human Gold AlignmentUnit types.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..domain import Language, ParagraphId, SourceId


PAIRWISE_ALIGNMENT_PROPOSAL_SCHEMA_VERSION = "1.0-draft"
PAIRWISE_ALIGNMENT_PROPOSAL_ARTIFACT_TYPE = "pairwise_alignment_proposal"
ALIGNMENT_EVALUATOR_VERSION = "1.0"


class AlignmentDirection(str, Enum):
    ZH_EN = "zh-en"
    ZH_RU = "zh-ru"

    @property
    def right_language(self) -> Language:
        return Language.EN if self is AlignmentDirection.ZH_EN else Language.RU


class CoverageMode(str, Enum):
    PARTIAL = "partial"
    COMPLETE = "complete"


def canonical_parameters_json(parameters: dict[str, Any]) -> str:
    """Return the exact canonical JSON used for producer-parameter identity."""
    try:
        return json.dumps(
            parameters, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("Producer parameters must be finite JSON data") from error


def parameters_sha256(parameters: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_parameters_json(parameters).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProposalSourceSnapshot:
    source_id: SourceId
    language: Language
    normalized_path: str
    normalized_sha256: str
    normalized_schema_version: str
    paragraph_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, SourceId) or not isinstance(self.language, Language):
            raise ValueError("Proposal source snapshot requires typed source and language identity")
        if not isinstance(self.normalized_path, str) or not isinstance(self.normalized_schema_version, str) or not self.normalized_path or not self.normalized_schema_version:
            raise ValueError("Proposal source snapshot requires normalized path and schema version")
        if not isinstance(self.normalized_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.normalized_sha256):
            raise ValueError("Proposal source snapshot requires a SHA-256")
        if isinstance(self.paragraph_count, bool) or not isinstance(self.paragraph_count, int) or self.paragraph_count < 0:
            raise ValueError("Proposal source paragraph count must be a non-negative integer")


@dataclass(frozen=True)
class ProducerProvenance:
    producer_id: str
    producer_version: str
    parameters: dict[str, Any]
    parameters_sha256: str
    code_revision: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.producer_id, str) or not isinstance(self.producer_version, str) or not self.producer_id.strip() or not self.producer_version.strip():
            raise ValueError("Producer ID and version are required")
        if not isinstance(self.parameters, dict) or any(not isinstance(key, str) for key in self.parameters):
            raise ValueError("Producer parameters must be a JSON object with string keys")
        expected = parameters_sha256(self.parameters)
        if self.parameters_sha256 != expected:
            raise ValueError("Producer parameters_sha256 does not match canonical parameters JSON")
        if self.code_revision is not None and (not isinstance(self.code_revision, str) or not self.code_revision.strip()):
            raise ValueError("Producer code_revision must be null or a non-empty string")


@dataclass(frozen=True)
class ProposalScore:
    name: str
    value: float
    semantics: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not isinstance(self.semantics, str) or not self.name.strip() or not self.semantics.strip():
            raise ValueError("Proposal score requires name and declared semantics")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)) or not math.isfinite(self.value):
            raise ValueError("Proposal score value must be finite")


@dataclass(frozen=True)
class ProposalSide:
    paragraphs: tuple[ParagraphId, ...] = ()
    unmatched: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.unmatched, bool):
            raise ValueError("Proposal unmatched state must be boolean")
        if any(not isinstance(item, ParagraphId) for item in self.paragraphs):
            raise ValueError("Proposal side paragraphs must be typed stable Paragraph IDs")
        if bool(self.paragraphs) == self.unmatched:
            raise ValueError("Proposal side must contain paragraphs or unmatched=true, exclusively")


@dataclass(frozen=True)
class ProposalUnit:
    id: str
    left: ProposalSide
    right: ProposalSide
    scores: tuple[ProposalScore, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not re.fullmatch(r"u\d{6}", self.id):
            raise ValueError(f"Invalid proposal unit ID: {self.id!r}")
        if not isinstance(self.left, ProposalSide) or not isinstance(self.right, ProposalSide):
            raise ValueError("Proposal unit requires typed left and right sides")
        if self.left.unmatched and self.right.unmatched:
            raise ValueError("A proposal unit cannot have both sides unmatched")
        if any(not isinstance(item, ProposalScore) for item in self.scores):
            raise ValueError("Proposal unit scores must use the typed score contract")


@dataclass(frozen=True)
class PairwiseAlignmentProposal:
    schema_version: str
    artifact_type: str
    work_id: str
    chapter: int
    direction: AlignmentDirection
    coverage_mode: CoverageMode
    left_source: ProposalSourceSnapshot
    right_source: ProposalSourceSnapshot
    producer: ProducerProvenance
    units: tuple[ProposalUnit, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != PAIRWISE_ALIGNMENT_PROPOSAL_SCHEMA_VERSION:
            raise ValueError(f"Unsupported proposal schema version: {self.schema_version!r}")
        if self.artifact_type != PAIRWISE_ALIGNMENT_PROPOSAL_ARTIFACT_TYPE:
            raise ValueError(f"Unsupported proposal artifact type: {self.artifact_type!r}")
        if not isinstance(self.direction, AlignmentDirection) or not isinstance(self.coverage_mode, CoverageMode):
            raise ValueError("Proposal requires typed canonical direction and coverage mode")
        if not isinstance(self.left_source, ProposalSourceSnapshot) or not isinstance(self.right_source, ProposalSourceSnapshot):
            raise ValueError("Proposal requires typed source snapshots")
        if not isinstance(self.producer, ProducerProvenance) or any(not isinstance(item, ProposalUnit) for item in self.units):
            raise ValueError("Proposal requires typed producer provenance and proposal units")
        if not isinstance(self.work_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", self.work_id):
            raise ValueError(f"Invalid proposal work ID: {self.work_id!r}")
        if isinstance(self.chapter, bool) or not isinstance(self.chapter, int) or self.chapter < 1:
            raise ValueError("Proposal chapter must be a positive integer")


def proposal_unit_id(index: int) -> str:
    if isinstance(index, bool) or not isinstance(index, int) or index < 1:
        raise ValueError("Proposal unit index must be positive")
    return f"u{index:06d}"
