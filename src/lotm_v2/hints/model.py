"""Provider-neutral representations for non-canonical local hints."""
from __future__ import annotations

from dataclasses import dataclass


HINT_SCHEMA_VERSION = "1.0-draft"


@dataclass(frozen=True)
class HintToken:
    index: int
    surface: str
    start_offset: int
    end_offset: int

    def __post_init__(self) -> None:
        if self.index < 0 or not self.surface:
            raise ValueError("Hint token requires a non-negative index and surface")
        if self.start_offset < 0 or self.end_offset <= self.start_offset:
            raise ValueError("Hint token offsets must be ordered half-open offsets")


@dataclass(frozen=True)
class LexicalAlignment:
    zh_token_start: int
    zh_token_end: int
    en_token_start: int
    en_token_end: int
    score: float

    def __post_init__(self) -> None:
        if min(self.zh_token_start, self.en_token_start) < 0:
            raise ValueError("Lexical alignment token offsets cannot be negative")
        if self.zh_token_end <= self.zh_token_start or self.en_token_end <= self.en_token_start:
            raise ValueError("Lexical alignment spans must be non-empty")
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("Lexical alignment score must be between zero and one")


@dataclass(frozen=True)
class HintBundle:
    schema_version: str
    paragraph_id: str
    source_text_sha256: str
    translation_provider: str
    segmentation_provider: str
    alignment_provider: str
    en_hint: str | None
    zh_tokens: tuple[HintToken, ...]
    en_tokens: tuple[HintToken, ...]
    alignments: tuple[LexicalAlignment, ...]
    segmentation_error: str | None = None
    translation_error: str | None = None
    alignment_error: str | None = None

    @property
    def available(self) -> bool:
        return self.en_hint is not None
