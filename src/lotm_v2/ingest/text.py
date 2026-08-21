"""Minimal immutable text ingest path for pilot corpus chapters."""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..domain import SourceChapter, SourceManifest
from ..infrastructure.corpus_io import save_manifest
from ..infrastructure.paths import PathPolicy


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def ingest_text_chapter(
    input_path: Path,
    manifest: SourceManifest,
    manifest_path: Path,
    chapter_number: int,
    paths: PathPolicy,
) -> tuple[SourceManifest, Path]:
    """Copy a source chapter once and reject later content-changing writes."""
    content = input_path.read_bytes()
    # Decode now so invalid text never enters the textual pilot corpus.
    content.decode("utf-8")
    digest = sha256_bytes(content)
    target = paths.raw_chapter(manifest.descriptor.source_id, chapter_number)
    if target.exists():
        if target.read_bytes() != content:
            raise ValueError(f"Immutable raw chapter already exists with different content: {target}")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    entry = SourceChapter(chapter_number, paths.relative(target), digest)
    updated = manifest.with_chapter(entry)
    save_manifest(manifest_path, updated)
    return updated, target

