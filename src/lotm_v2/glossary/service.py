"""Explicit human glossary workflow; never writes Gold."""
from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .io import glossary_to_dict, load_glossary, save_glossary
from .model import (
    GLOSSARY_SCHEMA_VERSION, Glossary, GlossaryAliases, GlossaryEntry,
    GlossaryProvenance, GlossarySourceRef, GlossaryStatus,
)


class GlossaryDuplicateError(ValueError):
    def __init__(self, entry: GlossaryEntry) -> None:
        super().__init__(f"Existing glossary entry found: {entry.zh_term}")
        self.entry = entry


class GlossaryService:
    def __init__(self, path: Path, work_id: str, tool_version: str = "review-hints-1") -> None:
        self.path = path.resolve()
        self.work_id = work_id
        self.tool_version = tool_version

    def load(self) -> Glossary:
        if not self.path.is_file():
            return Glossary(GLOSSARY_SCHEMA_VERSION, self.work_id)
        glossary = load_glossary(self.path)
        if glossary.schema_version != GLOSSARY_SCHEMA_VERSION or glossary.work_id != self.work_id:
            raise ValueError("Unsupported or mismatched glossary artifact")
        return glossary

    def list(self, query: str = "", status: str | None = None) -> dict[str, Any]:
        glossary = self.load()
        needle = query.strip().casefold()
        entries = [
            entry for entry in glossary.entries
            if (not needle or needle in entry.zh_term.casefold() or needle in entry.en_term.casefold()
                or (entry.ru_term and needle in entry.ru_term.casefold()))
            and (not status or entry.status.value == status)
        ]
        counts = {item.value: sum(entry.status == item for entry in glossary.entries) for item in GlossaryStatus}
        return {"schema_version": glossary.schema_version, "work_id": glossary.work_id,
                "counts": counts, "entries": [self.entry_payload(item) for item in entries]}

    def find(self, zh_term: str) -> GlossaryEntry | None:
        normalized = zh_term.strip()
        return next((
            entry for entry in self.load().entries
            if entry.zh_term == normalized or normalized in entry.aliases.zh
        ), None)

    def occurrences(self, zh_text: str) -> list[dict[str, Any]]:
        """Locate known canonical forms/aliases for optional UI highlighting."""
        found: list[dict[str, Any]] = []
        for entry in self.load().entries:
            forms = (entry.zh_term,) + entry.aliases.zh
            for form in forms:
                start = 0
                while form and (position := zh_text.find(form, start)) >= 0:
                    found.append({
                        "start_offset": position,
                        "end_offset": position + len(form),
                        "matched_form": form,
                        "entry": self.entry_payload(entry),
                    })
                    start = position + len(form)
        return sorted(found, key=lambda item: (item["start_offset"], item["end_offset"]))

    def preview(
        self, zh_term: str, en_term: str, chapter: int, paragraph_id: str,
        start_offset: int, end_offset: int, notes: str | None = None,
    ) -> dict[str, Any]:
        zh, en = zh_term.strip(), en_term.strip()
        if not zh or not en:
            raise ValueError("Glossary preview requires Chinese and English terms")
        source = GlossarySourceRef(self.work_id, chapter, paragraph_id, start_offset, end_offset)
        existing = self.find(zh)
        return {
            "duplicate": existing is not None,
            "existing": self.entry_payload(existing) if existing else None,
            "candidate": {
                "zh_term": zh, "en_term": en, "ru_term": None,
                "status": GlossaryStatus.NEEDS_RU.value,
                "source": self.source_payload(source), "notes": _optional(notes),
            },
        }

    def add(
        self, zh_term: str, en_term: str, chapter: int, paragraph_id: str,
        start_offset: int, end_offset: int, notes: str | None = None,
    ) -> GlossaryEntry:
        preview = self.preview(zh_term, en_term, chapter, paragraph_id, start_offset, end_offset, notes)
        if preview["duplicate"]:
            raise GlossaryDuplicateError(self.find(zh_term.strip()))  # type: ignore[arg-type]
        now = _now()
        provenance = GlossaryProvenance(now, "human", self.tool_version)
        entry = GlossaryEntry(
            id="term-" + hashlib.sha256(zh_term.strip().encode("utf-8")).hexdigest()[:16],
            zh_term=zh_term.strip(), en_term=en_term.strip(), ru_term=None,
            status=GlossaryStatus.NEEDS_RU, aliases=GlossaryAliases(),
            source_refs=(GlossarySourceRef(
                self.work_id, chapter, paragraph_id, start_offset, end_offset,
            ),), notes=_optional(notes), created=provenance, updated=provenance,
        )
        glossary = self.load()
        save_glossary(self.path, replace(glossary, entries=glossary.entries + (entry,)))
        return entry

    def add_alias(self, zh_term: str, language: str, alias: str) -> GlossaryEntry:
        if language not in {"zh", "en", "ru"} or not alias.strip():
            raise ValueError("Alias requires language zh/en/ru and non-empty text")
        glossary = self.load()
        existing = self.find(zh_term)
        if existing is None:
            raise ValueError("Glossary entry does not exist")
        values = list(getattr(existing.aliases, language))
        if alias.strip() not in values:
            values.append(alias.strip())
        aliases = replace(existing.aliases, **{language: tuple(values)})
        updated = replace(
            existing, aliases=aliases,
            updated=GlossaryProvenance(_now(), "human", self.tool_version),
        )
        entries = tuple(updated if item.id == existing.id else item for item in glossary.entries)
        save_glossary(self.path, replace(glossary, entries=entries))
        return updated

    @staticmethod
    def source_payload(source: GlossarySourceRef) -> dict[str, Any]:
        return {
            "work_id": source.work_id, "chapter": source.chapter,
            "paragraph_id": source.paragraph_id,
            "start_offset": source.start_offset, "end_offset": source.end_offset,
        }

    @staticmethod
    def entry_payload(entry: GlossaryEntry | None) -> dict[str, Any] | None:
        if entry is None:
            return None
        return glossary_to_dict(Glossary(GLOSSARY_SCHEMA_VERSION, entry.source_refs[0].work_id, (entry,)))["entries"][0]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _optional(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None
