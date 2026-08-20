from __future__ import annotations

import re


_FOOTNOTE_REFERENCE = re.compile(r"\s*\[\d{1,3}\]")
_NON_STORY_BLOCK = re.compile(r"^(?:read comments|discord|ps\s*[:：])", re.IGNORECASE)
_EDITOR_NOTE = re.compile(r"(?:прим\.\s*ред\.|примечани[ея]\s+переводчика|translator'?s\s+note)", re.IGNORECASE)
_EMPTY_DIALOGUE = {"…", "...", "«…»", "\"…\"", "\"...\""}
_KNOWN_CULTURAL_EXPANSIONS = (
    re.compile(r"^This is actually a proverb that describes Lord Ye\.", re.IGNORECASE),
    re.compile(r"^В период Весен и Осеней .*? называл себя Е-гун\.", re.IGNORECASE),
    re.compile(r"^Когда небесный дракон услышал об этом, он посетил Е-гуна", re.IGNORECASE),
    re.compile(r"^Дракон спросил: «Я слышал, что Вы любите драконов", re.IGNORECASE),
    re.compile(r"^Дрожа от страха, Е-гун ответил:", re.IGNORECASE),
)


def clean_story_text(text: str) -> str:
    """Remove clearly non-narrative markers while preserving story paragraphs."""
    text = _FOOTNOTE_REFERENCE.sub("", text)
    paragraphs = []
    for block in re.split(r"\n\s*\n", text):
        cleaned = "\n".join(line.strip() for line in block.splitlines() if line.strip()).strip()
        if (
            not cleaned
            or cleaned in _EMPTY_DIALOGUE
            or _NON_STORY_BLOCK.match(cleaned)
            or _EDITOR_NOTE.search(cleaned)
            or any(pattern.search(cleaned) for pattern in _KNOWN_CULTURAL_EXPANSIONS)
        ):
            continue
        paragraphs.append(cleaned)
    return "\n\n".join(paragraphs) + ("\n" if paragraphs else "")
