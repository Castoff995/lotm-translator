"""Pure helpers for contiguous, human-controlled glossary selections."""
from __future__ import annotations

from .model import HintToken


def select_contiguous(tokens: tuple[HintToken, ...], start: int, end: int) -> dict[str, object]:
    """Return a half-open token selection and its exact source character span."""
    if start < 0 or end <= start or end > len(tokens):
        raise ValueError("Token selection must be a non-empty contiguous range")
    chosen = tokens[start:end]
    if tuple(item.index for item in chosen) != tuple(range(start, end)):
        raise ValueError("Token indexes must be contiguous")
    return {
        "token_start": start,
        "token_end": end,
        "start_offset": chosen[0].start_offset,
        "end_offset": chosen[-1].end_offset,
        "surface": "".join(item.surface for item in chosen),
    }
