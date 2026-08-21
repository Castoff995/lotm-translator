"""Single serialization/deserialization boundary for gold JSON."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..domain import ChapterId, Language, ParagraphId, SourceId
from ..infrastructure.json_io import read_json, write_json
from .model import (
    BoundaryDecision, GoldAlignmentSide, GoldAlignmentUnit, GoldBoundary,
    GoldChapter, GoldFlag, GoldGap, GoldSourceRef, GoldStatus,
)


def _side_to_dict(side: GoldAlignmentSide) -> dict[str, Any]:
    if side.gap:
        return {"gap": {"reason": side.gap.reason.value, "note": side.gap.note}}
    return {"paragraphs": [str(item) for item in side.paragraphs]}


def _side_from_dict(payload: dict[str, Any]) -> GoldAlignmentSide:
    if "gap" in payload:
        gap = payload["gap"]
        return GoldAlignmentSide(gap=GoldGap(GoldFlag(str(gap["reason"])), gap.get("note")))
    return GoldAlignmentSide(paragraphs=tuple(ParagraphId.parse(str(item)) for item in payload.get("paragraphs", [])))


def gold_to_dict(gold: GoldChapter) -> dict[str, Any]:
    return {
        "schema_version": gold.schema_version,
        "status": gold.status.value,
        "work_id": gold.chapter.work_id,
        "chapter": gold.chapter.number,
        "sources": [
            {
                "source_id": str(source.source_id), "language": source.language.value,
                "normalized_path": source.normalized_path,
                "normalized_sha256": source.normalized_sha256,
            }
            for source in gold.sources
        ],
        "alignment_units": [
            {
                "id": unit.id, "zh": _side_to_dict(unit.zh), "en": _side_to_dict(unit.en),
                "ru": _side_to_dict(unit.ru), "flags": [flag.value for flag in unit.flags], "note": unit.note,
            }
            for unit in gold.alignment_units
        ],
        "boundaries": [
            {"after": boundary.after, "decision": boundary.decision.value, "note": boundary.note}
            for boundary in gold.boundaries
        ],
        "notes": gold.notes,
    }


def gold_from_dict(payload: dict[str, Any]) -> GoldChapter:
    chapter = ChapterId(str(payload["work_id"]), int(payload["chapter"]))
    sources = tuple(
        GoldSourceRef(
            SourceId(str(item["source_id"])), Language(str(item["language"])),
            str(item["normalized_path"]), str(item["normalized_sha256"]),
        )
        for item in payload.get("sources", [])
    )
    units = tuple(
        GoldAlignmentUnit(
            id=str(item["id"]), zh=_side_from_dict(item["zh"]), en=_side_from_dict(item["en"]),
            ru=_side_from_dict(item["ru"]),
            flags=tuple(GoldFlag(str(flag)) for flag in item.get("flags", [])), note=item.get("note"),
        )
        for item in payload.get("alignment_units", [])
    )
    boundaries = tuple(
        GoldBoundary(str(item["after"]), BoundaryDecision(str(item["decision"])), item.get("note"))
        for item in payload.get("boundaries", [])
    )
    return GoldChapter(
        schema_version=str(payload["schema_version"]), status=GoldStatus(str(payload["status"])),
        chapter=chapter, sources=sources, alignment_units=units, boundaries=boundaries,
        notes=payload.get("notes"),
    )


def save_gold(path: Path, gold: GoldChapter) -> None:
    write_json(path, gold_to_dict(gold))


def load_gold(path: Path) -> GoldChapter:
    return gold_from_dict(read_json(path))

