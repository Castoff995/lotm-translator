"""JSON codec for the versioned human glossary."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..infrastructure.json_io import read_json, write_json_atomic
from .model import (
    Glossary, GlossaryAliases, GlossaryEntry, GlossaryProvenance,
    GlossarySourceRef, GlossaryStatus,
)


def _provenance_to_dict(item: GlossaryProvenance) -> dict[str, Any]:
    return {"at": item.at, "actor": item.actor, "tool_version": item.tool_version}


def _provenance_from_dict(item: dict[str, Any]) -> GlossaryProvenance:
    return GlossaryProvenance(str(item["at"]), str(item["actor"]), str(item["tool_version"]))


def glossary_to_dict(glossary: Glossary) -> dict[str, Any]:
    return {
        "schema_version": glossary.schema_version,
        "work_id": glossary.work_id,
        "entries": [
            {
                "id": entry.id, "zh_term": entry.zh_term, "en_term": entry.en_term,
                "ru_term": entry.ru_term, "status": entry.status.value,
                "aliases": {
                    "zh": list(entry.aliases.zh), "en": list(entry.aliases.en),
                    "ru": list(entry.aliases.ru),
                },
                "source_refs": [
                    {
                        "work_id": ref.work_id, "chapter": ref.chapter,
                        "paragraph_id": ref.paragraph_id,
                        "start_offset": ref.start_offset, "end_offset": ref.end_offset,
                    }
                    for ref in entry.source_refs
                ],
                "notes": entry.notes,
                "created": _provenance_to_dict(entry.created),
                "updated": _provenance_to_dict(entry.updated),
            }
            for entry in glossary.entries
        ],
    }


def glossary_from_dict(payload: dict[str, Any]) -> Glossary:
    entries: list[GlossaryEntry] = []
    for item in payload.get("entries", []):
        aliases = item.get("aliases", {})
        entries.append(GlossaryEntry(
            id=str(item["id"]), zh_term=str(item["zh_term"]), en_term=str(item["en_term"]),
            ru_term=item.get("ru_term"), status=GlossaryStatus(str(item["status"])),
            aliases=GlossaryAliases(
                tuple(map(str, aliases.get("zh", []))),
                tuple(map(str, aliases.get("en", []))),
                tuple(map(str, aliases.get("ru", []))),
            ),
            source_refs=tuple(
                GlossarySourceRef(
                    str(ref["work_id"]), int(ref["chapter"]), str(ref["paragraph_id"]),
                    int(ref["start_offset"]), int(ref["end_offset"]),
                )
                for ref in item.get("source_refs", [])
            ),
            notes=item.get("notes"), created=_provenance_from_dict(item["created"]),
            updated=_provenance_from_dict(item["updated"]),
        ))
    return Glossary(str(payload["schema_version"]), str(payload["work_id"]), tuple(entries))


def load_glossary(path: Path) -> Glossary:
    return glossary_from_dict(read_json(path))


def save_glossary(path: Path, glossary: Glossary) -> None:
    write_json_atomic(path, glossary_to_dict(glossary))
