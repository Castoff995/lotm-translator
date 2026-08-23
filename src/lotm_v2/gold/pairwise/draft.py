"""Explicit empty-draft and hash-guarded confirmation lifecycle."""
from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path

from ...domain import Chapter, Language
from ...infrastructure.file_lock import ExclusiveFileLock
from ...infrastructure.paths import PathPolicy
from .io import (
    pairwise_gold_document_sha256, pairwise_gold_from_json_bytes,
    pairwise_gold_json_bytes, replace_pairwise_gold_atomic,
)
from .model import (
    PAIRWISE_GOLD_ARTIFACT_TYPE, PAIRWISE_GOLD_SCHEMA_VERSION,
    PairwiseCreationMode, PairwiseDirection, PairwiseGold, PairwiseGoldProvenance,
    PairwiseGoldSource, PairwiseGoldStatus,
)
from .validation import load_and_validate_pairwise_gold


def create_pairwise_gold_draft(
    left: Chapter, right: Chapter, left_path: Path, right_path: Path,
    direction: PairwiseDirection, paths: PathPolicy,
) -> PairwiseGold:
    if left.id != right.id:
        raise ValueError("Pairwise Gold draft sources must represent the same canonical chapter")
    if left.language is not Language.ZH or right.language is not direction.right_language:
        raise ValueError("Pairwise Gold draft source languages do not match canonical direction")
    left_path = paths.require_normalized_chapter_path(paths.relative(left_path), left.source, left.id.number)
    right_path = paths.require_normalized_chapter_path(paths.relative(right_path), right.source, right.id.number)
    source = lambda chapter, path: PairwiseGoldSource(
        chapter.source, chapter.language, paths.relative(path),
        hashlib.sha256(path.read_bytes()).hexdigest(), chapter.schema_version,
        len(chapter.paragraphs),
    )
    return PairwiseGold(
        PAIRWISE_GOLD_SCHEMA_VERSION, PAIRWISE_GOLD_ARTIFACT_TYPE,
        PairwiseGoldStatus.DRAFT, left.id.work_id, left.id.number, direction,
        source(left, left_path), source(right, right_path),
        provenance=PairwiseGoldProvenance(PairwiseCreationMode.EMPTY),
        notes="Empty Pairwise Gold draft; all decisions require explicit human review.",
    )


def confirm_pairwise_gold(
    path: Path, root: Path, expected_document_sha256: str,
    expected_file_sha256: str,
) -> PairwiseGold:
    paths = PathPolicy(root)
    resolved = path.resolve()
    try:
        resolved.relative_to(paths.root.resolve())
    except ValueError as error:
        raise ValueError("Pairwise Gold confirmation target escapes project root") from error
    with ExclusiveFileLock(paths.pairwise_resource_lock("gold", resolved)):
        source_bytes = resolved.read_bytes()
        if hashlib.sha256(source_bytes).hexdigest() != expected_file_sha256:
            raise ValueError("Pairwise Gold file SHA-256 changed; confirmation rejected")
        gold = pairwise_gold_from_json_bytes(source_bytes, str(resolved))
        paths.require_pairwise_gold_path(
            resolved, gold.left_source.source_id, gold.right_source.source_id, gold.chapter,
        )
        if gold.status is not PairwiseGoldStatus.DRAFT:
            raise ValueError("Only draft Pairwise Gold can be explicitly confirmed")
        if pairwise_gold_document_sha256(gold) != expected_document_sha256:
            raise ValueError("Pairwise Gold document SHA-256 changed; confirmation rejected")
        load_and_validate_pairwise_gold(gold, root, require_complete=True)
        active = paths.active_pairwise_review_session(
            gold.work_id, gold.left_source.source_id, gold.right_source.source_id, gold.chapter,
        )
        if active.exists():
            raise ValueError("An active Pairwise Review Session targets this draft; confirmation rejected")
        confirmed = replace(gold, status=PairwiseGoldStatus.CONFIRMED)
        replace_pairwise_gold_atomic(resolved, confirmed)
        written_bytes = resolved.read_bytes()
        if written_bytes != pairwise_gold_json_bytes(confirmed):
            raise ValueError("Pairwise Gold confirmation verification failed")
        written = pairwise_gold_from_json_bytes(written_bytes, str(resolved))
        load_and_validate_pairwise_gold(written, root, require_complete=True)
        return written
