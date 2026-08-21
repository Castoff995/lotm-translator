"""Expose an approved Russian pair alignment as canonical group units."""
from __future__ import annotations

import json
from pathlib import Path


def _paragraphs(path: Path) -> list[str]:
    return [part.strip() for part in path.read_text(encoding="utf-8").split("\n\n") if part.strip()]


def build_paired_group_units(
    alignment_path: Path,
    fan_path: Path,
    official_path: Path,
    output: Path,
    manifest: Path,
) -> int:
    """Create one canonical Russian unit per manually approved pair group.

    Official text is used as the semantic anchor when available; the manifest
    retains both Russian variants and their original paragraph numbers.
    """
    payload = json.loads(alignment_path.read_text(encoding="utf-8"))
    groups = payload.get("groups") or payload.get("alignments") or []
    fan, official = _paragraphs(fan_path), _paragraphs(official_path)
    units: list[str] = []
    records: list[dict[str, object]] = []
    for number, group in enumerate(groups, start=1):
        fan_numbers = list(group.get("left", []))
        official_numbers = list(group.get("right", []))
        # A single newline is visual only.  Double newlines delimit units for
        # Bertalign and must therefore occur only between approved groups.
        fan_text = "\n".join(fan[index - 1] for index in fan_numbers if 0 < index <= len(fan))
        official_text = "\n".join(official[index - 1] for index in official_numbers if 0 < index <= len(official))
        canonical = official_text or fan_text
        if not canonical:
            raise ValueError(f"Approved Russian group {number} has no text")
        units.append(canonical)
        records.append({"group": number, "fan_75": fan_numbers, "official": official_numbers, "fan_75_text": fan_text, "official_text": official_text})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n\n".join(units) + "\n", encoding="utf-8")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"alignment": str(alignment_path), "groups": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(units)
