"""Phase 3 pairwise proposal and read-only evaluation foundation."""

from .evaluation import evaluate_proposal_files
from .io import load_proposal, save_proposal
from .model import (
    ALIGNMENT_EVALUATOR_VERSION, PAIRWISE_ALIGNMENT_PROPOSAL_SCHEMA_VERSION,
    AlignmentDirection, CoverageMode, PairwiseAlignmentProposal, ProducerProvenance,
    ProposalScore, ProposalSide, ProposalSourceSnapshot, ProposalUnit,
    parameters_sha256, proposal_unit_id,
)
from .validation import ProposalValidationError, load_and_validate_proposal, validate_proposal

__all__ = [
    "ALIGNMENT_EVALUATOR_VERSION", "PAIRWISE_ALIGNMENT_PROPOSAL_SCHEMA_VERSION",
    "AlignmentDirection", "CoverageMode", "PairwiseAlignmentProposal", "ProducerProvenance",
    "ProposalScore", "ProposalSide", "ProposalSourceSnapshot", "ProposalUnit",
    "ProposalValidationError", "evaluate_proposal_files", "load_and_validate_proposal",
    "load_proposal", "parameters_sha256", "proposal_unit_id", "save_proposal",
    "validate_proposal",
]
