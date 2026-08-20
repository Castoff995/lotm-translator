from __future__ import annotations

import re


_FOOTNOTE_REFERENCE = re.compile(r"\s*\[\d{1,3}\]")


def clean_story_text(text: str) -> str:
    """Remove editorial footnote markers without rewriting story prose."""
    return _FOOTNOTE_REFERENCE.sub("", text)
