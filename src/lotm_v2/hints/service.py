"""Read-only orchestration for local paragraph-internal hints."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .cache import HintCache
from .model import HINT_SCHEMA_VERSION, HintBundle, HintToken, LexicalAlignment
from .providers import (
    ChineseSegmentationProvider, HintUnavailable, JiebaChineseSegmenter,
    OllamaTranslationHintProvider, RegexEnglishSegmenter,
    SimAlignTokenAlignmentProvider, TokenAlignmentProvider,
    TranslationHintProvider,
)


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def token_to_dict(token: HintToken) -> dict[str, Any]:
    return {
        "index": token.index, "surface": token.surface,
        "start_offset": token.start_offset, "end_offset": token.end_offset,
    }


def token_from_dict(payload: dict[str, Any]) -> HintToken:
    return HintToken(
        int(payload["index"]), str(payload["surface"]),
        int(payload["start_offset"]), int(payload["end_offset"]),
    )


def alignment_to_dict(alignment: LexicalAlignment) -> dict[str, Any]:
    return {
        "zh_token_start": alignment.zh_token_start,
        "zh_token_end": alignment.zh_token_end,
        "en_token_start": alignment.en_token_start,
        "en_token_end": alignment.en_token_end,
        "score": alignment.score,
    }


def alignment_from_dict(payload: dict[str, Any]) -> LexicalAlignment:
    return LexicalAlignment(
        int(payload["zh_token_start"]), int(payload["zh_token_end"]),
        int(payload["en_token_start"]), int(payload["en_token_end"]),
        float(payload["score"]),
    )


def bundle_to_dict(bundle: HintBundle) -> dict[str, Any]:
    return {
        "schema_version": bundle.schema_version,
        "paragraph_id": bundle.paragraph_id,
        "source_text_sha256": bundle.source_text_sha256,
        "translation_provider": bundle.translation_provider,
        "segmentation_provider": bundle.segmentation_provider,
        "alignment_provider": bundle.alignment_provider,
        "label": "LOCAL MACHINE TRANSLATION HINT",
        "available": bundle.available,
        "en_hint": bundle.en_hint,
        "zh_tokens": [token_to_dict(item) for item in bundle.zh_tokens],
        "en_tokens": [token_to_dict(item) for item in bundle.en_tokens],
        "alignments": [alignment_to_dict(item) for item in bundle.alignments],
        "segmentation_error": bundle.segmentation_error,
        "translation_error": bundle.translation_error,
        "alignment_error": bundle.alignment_error,
    }


class HintService:
    """Has no Gold dependency; inputs are an ID and already displayed text."""

    def __init__(
        self, cache_root: Path,
        translation_provider: TranslationHintProvider | None = None,
        segmentation_provider: ChineseSegmentationProvider | None = None,
        alignment_provider: TokenAlignmentProvider | None = None,
    ) -> None:
        self.cache = HintCache(cache_root)
        self.translation = translation_provider or OllamaTranslationHintProvider()
        self.segmentation = segmentation_provider or JiebaChineseSegmenter()
        self.alignment = alignment_provider or SimAlignTokenAlignmentProvider()
        self.english_segmentation = RegexEnglishSegmenter()

    def get(self, paragraph_id: str, zh_text: str) -> HintBundle:
        source_hash = text_sha256(zh_text)
        try:
            zh_tokens = self._segment(zh_text, source_hash)
        except HintUnavailable as error:
            return HintBundle(
                HINT_SCHEMA_VERSION, paragraph_id, source_hash,
                self.translation.provider_id, self.segmentation.provider_id,
                self.alignment.provider_id, None, (), (), (),
                segmentation_error=str(error),
            )
        try:
            en_hint = self._translate(zh_text, source_hash)
            translation_error = None
        except HintUnavailable as error:
            return HintBundle(
                HINT_SCHEMA_VERSION, paragraph_id, source_hash,
                self.translation.provider_id, self.segmentation.provider_id,
                self.alignment.provider_id, None, zh_tokens, (), (),
                translation_error=str(error),
            )
        en_tokens = self.english_segmentation.segment(en_hint)
        try:
            alignments = self._align(zh_text, en_hint, source_hash, zh_tokens, en_tokens)
            alignment_error = None
        except HintUnavailable as error:
            alignments = ()
            alignment_error = str(error)
        return HintBundle(
            HINT_SCHEMA_VERSION, paragraph_id, source_hash,
            self.translation.provider_id, self.segmentation.provider_id,
            self.alignment.provider_id, en_hint, zh_tokens, en_tokens, alignments,
            translation_error=translation_error, alignment_error=alignment_error,
        )

    def _segment(self, text: str, source_hash: str) -> tuple[HintToken, ...]:
        payload = {"text_sha256": source_hash, "source_language": "zh"}
        key = self.cache.key("segmentation", self.segmentation.provider_id, payload)
        cached = self.cache.get("segmentation", key)
        if cached is not None:
            return tuple(token_from_dict(item) for item in cached["tokens"])
        try:
            tokens = self.segmentation.segment(text)
        except HintUnavailable:
            raise
        self.cache.put("segmentation", key, {
            "provider_id": self.segmentation.provider_id,
            "text_sha256": source_hash,
            "tokens": [token_to_dict(item) for item in tokens],
        })
        return tokens

    def _translate(self, text: str, source_hash: str) -> str:
        payload = {
            "normalized_text_sha256": source_hash,
            "source_language": "zh", "target_language": "en",
        }
        key = self.cache.key("translation", self.translation.provider_id, payload)
        cached = self.cache.get("translation", key)
        if cached is not None:
            return str(cached["text"])
        translated = self.translation.translate(text, "zh", "en")
        self.cache.put("translation", key, {
            "provider_id": self.translation.provider_id,
            "normalized_text_sha256": source_hash,
            "source_language": "zh", "target_language": "en",
            "text": translated,
        })
        return translated

    def _align(
        self, zh_text: str, en_text: str, source_hash: str,
        zh_tokens: tuple[HintToken, ...], en_tokens: tuple[HintToken, ...],
    ) -> tuple[LexicalAlignment, ...]:
        en_hash = text_sha256(en_text)
        payload = {
            "zh_text_sha256": source_hash, "en_text_sha256": en_hash,
            "zh_segmentation_provider": self.segmentation.provider_id,
            "en_segmentation_provider": self.english_segmentation.provider_id,
        }
        key = self.cache.key("alignment", self.alignment.provider_id, payload)
        cached = self.cache.get("alignment", key)
        if cached is not None:
            return tuple(alignment_from_dict(item) for item in cached["alignments"])
        alignments = self.alignment.align(zh_text, en_text, zh_tokens, en_tokens)
        self.cache.put("alignment", key, {
            "provider_id": self.alignment.provider_id,
            "zh_text_sha256": source_hash, "en_text_sha256": en_hash,
            "alignments": [alignment_to_dict(item) for item in alignments],
        })
        return alignments
