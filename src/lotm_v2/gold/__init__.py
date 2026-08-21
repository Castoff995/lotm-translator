"""Human-reviewed gold schema and validation; no alignment generation."""

from .model import (
    BoundaryDecision, GoldAlignmentSide, GoldAlignmentUnit, GoldBoundary,
    GoldChapter, GoldFlag, GoldGap, GoldSourceRef, GoldStatus,
)
from .validation import GoldValidationError, validate_gold_chapter

__all__ = [
    "BoundaryDecision", "GoldAlignmentSide", "GoldAlignmentUnit", "GoldBoundary",
    "GoldChapter", "GoldFlag", "GoldGap", "GoldSourceRef", "GoldStatus",
    "GoldValidationError", "validate_gold_chapter",
]
