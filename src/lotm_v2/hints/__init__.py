"""Optional Phase 2 paragraph-internal lexical assistance; never Gold truth."""

from .model import HintBundle, HintToken, LexicalAlignment
from .providers import (
    ChineseSegmentationProvider, RussianTermSuggestionProvider,
    TokenAlignmentProvider, TranslationHintProvider,
)
from .service import HintService, bundle_to_dict

__all__ = [
    "ChineseSegmentationProvider", "HintBundle", "HintService", "HintToken", "bundle_to_dict",
    "LexicalAlignment", "RussianTermSuggestionProvider", "TokenAlignmentProvider",
    "TranslationHintProvider",
]
