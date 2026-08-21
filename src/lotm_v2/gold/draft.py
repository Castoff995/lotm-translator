"""Create empty human-review drafts without claiming automatic gold truth."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .. import GOLD_SCHEMA_VERSION
from ..domain import Chapter, Language
from ..infrastructure.paths import PathPolicy
from .model import GoldChapter, GoldSourceRef, GoldStatus


def create_gold_draft(chapters: tuple[Chapter, ...], normalized_paths: tuple[Path, ...], paths: PathPolicy) -> GoldChapter:
    if not chapters or len(chapters) != len(normalized_paths):
        raise ValueError("Gold draft requires matching normalized chapters and paths")
    chapter_id = chapters[0].id
    if any(chapter.id != chapter_id for chapter in chapters):
        raise ValueError("All gold draft sources must represent the same chapter")
    if {chapter.language for chapter in chapters} != {Language.ZH, Language.EN, Language.RU}:
        raise ValueError("Pilot gold draft requires exactly one ZH, EN, and RU normalized chapter")
    sources = tuple(
        GoldSourceRef(
            source_id=chapter.source, language=chapter.language,
            normalized_path=paths.relative(path),
            normalized_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for chapter, path in zip(chapters, normalized_paths)
    )
    return GoldChapter(
        schema_version=GOLD_SCHEMA_VERSION, status=GoldStatus.DRAFT,
        chapter=chapter_id, sources=sources,
        notes="Generated draft. Alignment units and JOIN/BREAK boundaries require human review.",
    )
