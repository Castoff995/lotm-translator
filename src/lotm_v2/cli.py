"""Dependency-light CLI for Architecture v2 phases 1–2."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from . import SOURCE_MANIFEST_SCHEMA_VERSION
from .domain import Chapter, Language, SourceDescriptor, SourceFormat, SourceId, SourceManifest, SourceRole
from .gold.model import GoldChapter
from .gold.draft import create_gold_draft
from .gold.io import load_gold, save_gold
from .gold.validation import GoldValidationError, validate_gold_chapter
from .infrastructure.corpus_io import load_chapter, load_manifest, save_chapter, save_manifest
from .infrastructure.paths import PathPolicy
from .ingest import ingest_text_chapter
from .normalize import normalize_manifest_chapter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LOTM Translator v2 foundation and gold tooling")
    commands = parser.add_subparsers(dest="command", required=True)

    manifest = commands.add_parser("manifest-init", help="Create a versioned source manifest")
    manifest.add_argument("output", type=Path)
    manifest.add_argument("--source-id", required=True)
    manifest.add_argument("--work-id", default="lotm")
    manifest.add_argument("--language", choices=[item.value for item in Language], required=True)
    manifest.add_argument("--role", choices=[item.value for item in SourceRole], required=True)
    manifest.add_argument("--edition", required=True)
    manifest.add_argument("--format", choices=[item.value for item in SourceFormat], required=True)
    manifest.add_argument("--normalization-version", default="2.0")

    ingest = commands.add_parser("ingest-text", help="Import one UTF-8 chapter into immutable v2 raw storage")
    ingest.add_argument("manifest", type=Path)
    ingest.add_argument("input", type=Path)
    ingest.add_argument("--chapter", type=int, required=True)
    ingest.add_argument("--root", type=Path, default=Path.cwd())

    normalize = commands.add_parser("normalize", help="Conservatively normalize one ingested chapter")
    normalize.add_argument("manifest", type=Path)
    normalize.add_argument("--chapter", type=int, required=True)
    normalize.add_argument("--root", type=Path, default=Path.cwd())
    normalize.add_argument("--output", type=Path)

    draft = commands.add_parser("gold-draft", help="Create an empty draft from normalized chapter JSON files")
    draft.add_argument("normalized", type=Path, nargs="+")
    draft.add_argument("--output", type=Path)
    draft.add_argument("--root", type=Path, default=Path.cwd())

    validate = commands.add_parser("gold-validate", help="Validate a manually edited gold chapter")
    validate.add_argument("gold", type=Path)
    validate.add_argument("--root", type=Path, default=Path.cwd())
    return parser


def _load_gold_sources(gold_path: Path, root: Path) -> tuple[GoldChapter, tuple[Chapter, ...]]:
    gold = load_gold(gold_path)
    paths = PathPolicy(root)
    chapters = []
    for source in gold.sources:
        path = paths.resolve(source.normalized_path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != source.normalized_sha256:
            raise ValueError(f"Normalized source checksum mismatch: {path}")
        chapters.append(load_chapter(path))
    return gold, tuple(chapters)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "manifest-init":
            manifest = SourceManifest(
                schema_version=SOURCE_MANIFEST_SCHEMA_VERSION,
                descriptor=SourceDescriptor(
                    source_id=SourceId(args.source_id), work_id=args.work_id,
                    language=Language(args.language), role=SourceRole(args.role),
                    edition=args.edition, source_format=SourceFormat(args.format),
                    normalization_version=args.normalization_version,
                ),
            )
            save_manifest(args.output, manifest)
            print(f"Source manifest created: {args.output}")
        elif args.command == "ingest-text":
            manifest = load_manifest(args.manifest)
            _, output = ingest_text_chapter(args.input, manifest, args.manifest, args.chapter, PathPolicy(args.root))
            print(f"Immutable raw chapter ingested: {output}")
        elif args.command == "normalize":
            paths = PathPolicy(args.root)
            manifest = load_manifest(args.manifest)
            chapter = normalize_manifest_chapter(manifest, args.manifest, args.chapter, paths)
            output = args.output or paths.normalized_chapter(chapter.source, args.chapter)
            save_chapter(output, chapter)
            print(f"Normalized chapter created: {output} ({len(chapter.paragraphs)} paragraphs)")
        elif args.command == "gold-draft":
            paths = PathPolicy(args.root)
            normalized_paths = tuple(path.resolve() for path in args.normalized)
            chapters = tuple(load_chapter(path) for path in normalized_paths)
            gold = create_gold_draft(chapters, normalized_paths, paths)
            output = args.output or paths.gold_chapter(gold.chapter.number)
            save_gold(output, gold)
            print(f"Gold draft created: {output}; status=draft; no alignment was inferred")
        elif args.command == "gold-validate":
            gold, chapters = _load_gold_sources(args.gold, args.root)
            validate_gold_chapter(gold, chapters)
            print(f"Gold chapter is valid: {args.gold} ({gold.status.value})")
        return 0
    except (OSError, KeyError, ValueError, GoldValidationError) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
