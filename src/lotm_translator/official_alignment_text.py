"""Prepare OCR-reviewed official Russian for paragraph alignment without editing it."""
from __future__ import annotations

from pathlib import Path


def prepare_official_alignment_text(source: Path, output: Path) -> int:
    # Apple Live Text preserves each recovered text line with a single newline.
    # BGE's paragraph reader uses blank lines, so expose those lines as atomic
    # alignment units in a derived copy. The reviewed OCR source is untouched.
    lines = [line.strip() for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)
