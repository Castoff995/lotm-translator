"""Deterministic protocol-v1 evaluation against independent human Pairwise Gold."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from typing import Any

from ..gold.pairwise.io import (
    pairwise_gold_document_sha256, pairwise_gold_from_json_bytes,
)
from ..gold.pairwise.model import PairwiseAlignmentSide, PairwiseGoldStatus
from ..gold.pairwise.validation import ValidatedPairwiseGold, load_and_validate_pairwise_gold
from ..infrastructure.json_io import decode_json_object
from ..infrastructure.paths import PathPolicy
from .io import proposal_document_sha256, proposal_from_json_bytes
from .model import ALIGNMENT_EVALUATOR_VERSION, ProposalSide
from .validation import ValidatedProposal, load_and_validate_proposal


PairwiseSignature = tuple[tuple[str, ...] | None, tuple[str, ...] | None]


def _proposal_side_signature(side: ProposalSide) -> tuple[str, ...] | None:
    return None if side.unmatched else tuple(str(item) for item in side.paragraphs)


def _gold_side_signature(side: PairwiseAlignmentSide) -> tuple[str, ...] | None:
    return None if side.gap else tuple(str(item) for item in side.paragraphs)


def _candidate_signatures(validated: ValidatedProposal) -> tuple[PairwiseSignature, ...]:
    return tuple(
        (_proposal_side_signature(unit.left), _proposal_side_signature(unit.right))
        for unit in validated.proposal.units
    )


def _gold_signatures(validated: ValidatedPairwiseGold) -> tuple[PairwiseSignature, ...]:
    return tuple(
        (_gold_side_signature(unit.left), _gold_side_signature(unit.right))
        for unit in validated.gold.alignment_units
    )


def _ratio(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 and numerator == 0 else numerator / denominator


def _exact_metrics(
    scored_candidate: tuple[PairwiseSignature, ...], gold: tuple[PairwiseSignature, ...],
    total_proposal_unit_count: int,
) -> dict[str, Any]:
    scored_count, gold_count = len(scored_candidate), len(gold)
    matches = sum((Counter(scored_candidate) & Counter(gold)).values())
    if scored_count == 0 and gold_count == 0:
        precision = recall = f1 = 1.0
    elif scored_count == 0 or gold_count == 0:
        precision = recall = f1 = 0.0
    else:
        precision = matches / scored_count
        recall = matches / gold_count
        f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return {
        "matches": matches, "total_proposal_unit_count": total_proposal_unit_count,
        "scored_candidate_unit_count": scored_count, "gold_count": gold_count,
        "precision": precision, "recall": recall, "f1": f1,
    }


def _covered_ids(signatures: tuple[PairwiseSignature, ...], side_index: int) -> set[str]:
    return {
        paragraph_id for signature in signatures
        for paragraph_id in (signature[side_index] or ())
    }


def _coverage_side(
    source_ids: tuple[str, ...], candidate_covered: set[str], gold_alignable: set[str],
) -> dict[str, Any]:
    source_count = len(source_ids)
    alignable_covered = candidate_covered & gold_alignable
    return {
        "source_paragraph_count": source_count,
        "candidate_covered_paragraph_count": len(candidate_covered),
        "candidate_source_coverage": _ratio(len(candidate_covered), source_count),
        "candidate_skipped_paragraph_count": source_count - len(candidate_covered),
        "gold_alignable_paragraph_count": len(gold_alignable),
        "candidate_covered_gold_alignable_count": len(alignable_covered),
        "gold_alignable_coverage": _ratio(len(alignable_covered), len(gold_alignable)),
        "missing_gold_alignable_paragraph_count": len(gold_alignable - candidate_covered),
    }


@dataclass(frozen=True)
class _DispositionClassification:
    scored_signatures: tuple[PairwiseSignature, ...]
    correctly_isolated_unit_count: int
    intrusion_unit_count: int
    correctly_isolated_left_ids: frozenset[str]
    correctly_isolated_right_ids: frozenset[str]
    intrusion_left_ids: frozenset[str]
    intrusion_right_ids: frozenset[str]


def _classify_dispositions(
    candidate: tuple[PairwiseSignature, ...], left_disposition_ids: tuple[str, ...],
    right_disposition_ids: tuple[str, ...],
) -> _DispositionClassification:
    left_dispositions, right_dispositions = set(left_disposition_ids), set(right_disposition_ids)
    scored: list[PairwiseSignature] = []
    isolated_left: set[str] = set()
    isolated_right: set[str] = set()
    intrusion_left: set[str] = set()
    intrusion_right: set[str] = set()
    isolated_units = intrusion_units = 0
    for signature in candidate:
        left_ids, right_ids = set(signature[0] or ()), set(signature[1] or ())
        left_hits, right_hits = left_ids & left_dispositions, right_ids & right_dispositions
        isolated_on_left = signature[0] is not None and left_ids <= left_dispositions and signature[1] is None
        isolated_on_right = signature[0] is None and signature[1] is not None and right_ids <= right_dispositions
        if isolated_on_left or isolated_on_right:
            isolated_units += 1
            isolated_left.update(left_ids)
            isolated_right.update(right_ids)
            continue
        scored.append(signature)
        if left_hits or right_hits:
            intrusion_units += 1
            intrusion_left.update(left_hits)
            intrusion_right.update(right_hits)
    return _DispositionClassification(
        tuple(scored), isolated_units, intrusion_units,
        frozenset(isolated_left), frozenset(isolated_right),
        frozenset(intrusion_left), frozenset(intrusion_right),
    )


def _disposition_side(
    source_ids: tuple[str, ...], gold_ids: tuple[str, ...], isolated_ids: frozenset[str],
    intrusion_ids: frozenset[str],
) -> dict[str, Any]:
    isolated = [item for item in source_ids if item in isolated_ids]
    intrusions = [item for item in source_ids if item in intrusion_ids]
    return {
        "gold_disposition_paragraph_count": len(gold_ids),
        "correctly_isolated_disposition_paragraph_count": len(isolated),
        "correctly_isolated_disposition_paragraph_ids": isolated,
        "disposition_intrusion_paragraph_count": len(intrusions),
        "disposition_intrusion_ids": intrusions,
    }


def _require_same_benchmark(proposal: ValidatedProposal, benchmark: ValidatedPairwiseGold) -> None:
    candidate, gold = proposal.proposal, benchmark.gold
    issues: list[str] = []
    if candidate.work_id != gold.work_id or candidate.chapter != gold.chapter:
        issues.append("Proposal and Pairwise Gold work/chapter differ")
    if candidate.direction.value != gold.direction.value:
        issues.append("Proposal and Pairwise Gold directions differ")
    for side_name, proposal_source, gold_source, proposal_path, gold_path in (
        ("left", candidate.left_source, gold.left_source, proposal.left_path, benchmark.left_path),
        ("right", candidate.right_source, gold.right_source, proposal.right_path, benchmark.right_path),
    ):
        if proposal_source.source_id != gold_source.source_id or proposal_source.language is not gold_source.language:
            issues.append(f"Proposal and Pairwise Gold {side_name} exact source identity differ")
        if os.path.normcase(str(proposal_path.resolve())) != os.path.normcase(str(gold_path.resolve())):
            issues.append(f"Proposal and Pairwise Gold {side_name} normalized paths differ")
        if proposal_source.normalized_sha256 != gold_source.normalized_sha256:
            issues.append(f"Proposal and Pairwise Gold {side_name} normalized hashes differ")
        if proposal_source.normalized_schema_version != gold_source.normalized_schema_version:
            issues.append(f"Proposal and Pairwise Gold {side_name} normalized schema versions differ")
        if proposal_source.paragraph_count != gold_source.paragraph_count:
            issues.append(f"Proposal and Pairwise Gold {side_name} paragraph counts differ")
    if issues:
        raise ValueError("Alignment evaluation benchmark mismatch:\n- " + "\n- ".join(issues))


def evaluate_validated_proposal(
    proposal: ValidatedProposal, benchmark: ValidatedPairwiseGold, *,
    allow_draft_gold: bool = False, proposal_file_hash: str | None = None,
    gold_file_hash: str | None = None,
) -> dict[str, Any]:
    gold = benchmark.gold
    if gold.status is not PairwiseGoldStatus.CONFIRMED and not allow_draft_gold:
        raise ValueError(
            "Authoritative alignment evaluation requires confirmed Pairwise Gold; "
            "use --allow-draft-gold only for development evaluation"
        )
    _require_same_benchmark(proposal, benchmark)
    candidate = _candidate_signatures(proposal)
    gold_signatures = _gold_signatures(benchmark)
    disposition_set = {str(item.paragraph_id) for item in gold.paragraph_dispositions}
    left_dispositions = tuple(
        str(item.id) for item in benchmark.left_chapter.paragraphs if str(item.id) in disposition_set
    )
    right_dispositions = tuple(
        str(item.id) for item in benchmark.right_chapter.paragraphs if str(item.id) in disposition_set
    )
    classification = _classify_dispositions(candidate, left_dispositions, right_dispositions)
    scored = classification.scored_signatures
    candidate_counter, gold_counter = Counter(scored), Counter(gold_signatures)
    exact_unmatched = sum(
        min(candidate_counter[item], gold_counter[item])
        for item in set(candidate_counter) | set(gold_counter)
        if item[0] is None or item[1] is None
    )
    left_candidate, right_candidate = _covered_ids(candidate, 0), _covered_ids(candidate, 1)
    left_gold, right_gold = _covered_ids(gold_signatures, 0), _covered_ids(gold_signatures, 1)
    left_ids = tuple(str(item.id) for item in proposal.left_chapter.paragraphs)
    right_ids = tuple(str(item.id) for item in proposal.right_chapter.paragraphs)
    machine = proposal.proposal
    return {
        "evaluator_version": ALIGNMENT_EVALUATOR_VERSION,
        "benchmark_authority": "confirmed" if gold.status is PairwiseGoldStatus.CONFIRMED else "development_draft",
        "direction": machine.direction.value, "work_id": machine.work_id, "chapter": machine.chapter,
        "proposal": {
            "schema_version": machine.schema_version, "artifact_type": machine.artifact_type,
            "document_sha256": proposal_document_sha256(machine), "file_sha256": proposal_file_hash,
            "producer_id": machine.producer.producer_id,
            "producer_version": machine.producer.producer_version,
            "parameters_sha256": machine.producer.parameters_sha256,
            "code_revision": machine.producer.code_revision,
            "coverage_mode": machine.coverage_mode.value,
        },
        "pairwise_gold": {
            "schema_version": gold.schema_version, "artifact_type": gold.artifact_type,
            "status": gold.status.value, "document_sha256": pairwise_gold_document_sha256(gold),
            "file_sha256": gold_file_hash,
            "left_source_id": str(gold.left_source.source_id),
            "right_source_id": str(gold.right_source.source_id),
        },
        "sources": {
            "left": {
                "source_id": str(gold.left_source.source_id),
                "normalized_sha256": gold.left_source.normalized_sha256,
                "normalized_schema_version": gold.left_source.normalized_schema_version,
                "paragraph_count": gold.left_source.paragraph_count,
            },
            "right": {
                "source_id": str(gold.right_source.source_id),
                "normalized_sha256": gold.right_source.normalized_sha256,
                "normalized_schema_version": gold.right_source.normalized_schema_version,
                "paragraph_count": gold.right_source.paragraph_count,
            },
        },
        "validation": {
            "proposal_valid": True, "pairwise_gold_fully_validated": True,
            "source_identity_verified": True, "normalized_hashes_verified": True,
        },
        "exact_units": _exact_metrics(scored, gold_signatures, len(candidate)),
        "coverage": {
            "left": _coverage_side(left_ids, left_candidate, left_gold),
            "right": _coverage_side(right_ids, right_candidate, right_gold),
        },
        "dispositions": {
            "correctly_isolated_disposition_unit_count": classification.correctly_isolated_unit_count,
            "disposition_intrusion_unit_count": classification.intrusion_unit_count,
            "left": _disposition_side(
                left_ids, left_dispositions, classification.correctly_isolated_left_ids,
                classification.intrusion_left_ids,
            ),
            "right": _disposition_side(
                right_ids, right_dispositions, classification.correctly_isolated_right_ids,
                classification.intrusion_right_ids,
            ),
        },
        "unmatched": {
            "total_candidate_unmatched_unit_count": sum(1 for item in candidate if item[0] is None or item[1] is None),
            "scored_candidate_unmatched_unit_count": sum(1 for item in scored if item[0] is None or item[1] is None),
            "gold_pairwise_gap_unit_count": sum(1 for item in gold_signatures if item[0] is None or item[1] is None),
            "exact_unmatched_unit_matches": exact_unmatched,
        },
    }


def evaluate_proposal_files(
    proposal_path: Path, gold_path: Path, root: Path, *, allow_draft_gold: bool = False,
) -> dict[str, Any]:
    """Read and validate exact proposal/Pairwise-Gold revisions before scoring."""
    proposal_bytes, gold_bytes = proposal_path.read_bytes(), gold_path.read_bytes()
    raw_gold = decode_json_object(gold_bytes, str(gold_path))
    if raw_gold.get("artifact_type") != "pairwise_gold":
        raise ValueError("alignment-evaluate requires independent Pairwise Gold; trilingual Gold is not accepted")
    proposal = proposal_from_json_bytes(proposal_bytes, str(proposal_path))
    validated_proposal = load_and_validate_proposal(proposal, root)
    gold = pairwise_gold_from_json_bytes(gold_bytes, str(gold_path))
    PathPolicy(root).require_pairwise_gold_path(
        gold_path, gold.left_source.source_id, gold.right_source.source_id, gold.chapter,
    )
    validated_gold = load_and_validate_pairwise_gold(gold, root, require_complete=True)
    return evaluate_validated_proposal(
        validated_proposal, validated_gold, allow_draft_gold=allow_draft_gold,
        proposal_file_hash=hashlib.sha256(proposal_bytes).hexdigest(),
        gold_file_hash=hashlib.sha256(gold_bytes).hexdigest(),
    )
