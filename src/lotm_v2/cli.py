"""Dependency-light CLI for Architecture v2 phases 1–2."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from . import NORMALIZED_SCHEMA_VERSION, SOURCE_MANIFEST_SCHEMA_VERSION
from .domain import Chapter, Language, ParagraphizationMode, SourceDescriptor, SourceFormat, SourceId, SourceManifest, SourceRole
from .gold.model import GoldChapter
from .gold.draft import create_gold_draft
from .gold.io import load_gold, save_gold
from .gold.validation import GoldValidationError, validate_gold_chapter
from .infrastructure.corpus_io import load_chapter, load_manifest, save_chapter, save_manifest
from .infrastructure.paths import PathPolicy
from .ingest import ingest_epub_package, ingest_text_chapter, inspect_epub
from .normalize import normalize_manifest_chapter
from .normalize.paragraphization import load_artifact, register_artifact, validate_artifact


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
    manifest.add_argument("--normalization-version", default=NORMALIZED_SCHEMA_VERSION)
    manifest.add_argument("--paragraphization-mode", choices=[item.value for item in ParagraphizationMode], default="blank_lines")

    ingest = commands.add_parser("ingest-text", help="Import one UTF-8 chapter into immutable v2 raw storage")
    ingest.add_argument("manifest", type=Path)
    ingest.add_argument("input", type=Path)
    ingest.add_argument("--chapter", type=int, required=True)
    ingest.add_argument("--root", type=Path, default=Path.cwd())

    epub_inspect = commands.add_parser("epub-inspect", help="Inspect EPUB package, spine and navigation without corpus mutation")
    epub_inspect.add_argument("book", type=Path)

    ingest_epub = commands.add_parser("ingest-epub", help="Import one immutable EPUB package authority")
    ingest_epub.add_argument("manifest", type=Path)
    ingest_epub.add_argument("book", type=Path)
    ingest_epub.add_argument("--chapter", type=int, required=True)
    ingest_epub.add_argument("--root", type=Path, default=Path.cwd())

    epub_generate = commands.add_parser("epub-artifact-generate", help="Generate a reviewable EPUB DOM paragraphization proposal")
    epub_generate.add_argument("manifest", type=Path)
    epub_generate.add_argument("book", type=Path)
    epub_generate.add_argument("--chapter", type=int, required=True)
    epub_generate.add_argument("--toc-index", type=int, help="Explicit navigation entry index when chapter labels are ambiguous")
    epub_generate.add_argument("--output", type=Path, required=True)
    epub_generate.add_argument("--root", type=Path, default=Path.cwd())

    epub_check = commands.add_parser("epub-artifact-check", help="Validate EPUB DOM selectors, hashes, order and coverage")
    epub_check.add_argument("manifest", type=Path)
    epub_check.add_argument("artifact", type=Path)
    epub_check.add_argument("--chapter", type=int, required=True)
    epub_check.add_argument("--root", type=Path, default=Path.cwd())

    epub_register = commands.add_parser("epub-artifact-register", help="Validate and explicitly freeze an EPUB paragraphization artifact")
    epub_register.add_argument("manifest", type=Path)
    epub_register.add_argument("artifact", type=Path)
    epub_register.add_argument("--chapter", type=int, required=True)
    epub_register.add_argument("--root", type=Path, default=Path.cwd())

    map_generate = commands.add_parser("chapter-map-generate", help="Generate a source navigation to canonical chapter proposal")
    map_generate.add_argument("manifest", type=Path)
    map_generate.add_argument("--output", type=Path, required=True)
    map_generate.add_argument("--canonical-start", type=int, default=1)
    map_generate.add_argument("--root", type=Path, default=Path.cwd())

    map_check = commands.add_parser("chapter-map-check", help="Validate a Source Chapter Map proposal")
    map_check.add_argument("manifest", type=Path)
    map_check.add_argument("chapter_map", type=Path)
    map_check.add_argument("--root", type=Path, default=Path.cwd())

    map_register = commands.add_parser("chapter-map-register", help="Validate and freeze a Source Chapter Map")
    map_register.add_argument("manifest", type=Path)
    map_register.add_argument("chapter_map", type=Path)
    map_register.add_argument("--root", type=Path, default=Path.cwd())

    map_inspect = commands.add_parser("chapter-map-inspect", help="Show registered Chapter Map identity summary")
    map_inspect.add_argument("manifest", type=Path)
    map_inspect.add_argument("--root", type=Path, default=Path.cwd())

    normalize = commands.add_parser("normalize", help="Conservatively normalize one ingested chapter")
    normalize.add_argument("manifest", type=Path)
    normalize.add_argument("--chapter", type=int, required=True)
    normalize.add_argument("--root", type=Path, default=Path.cwd())
    normalize.add_argument("--output", type=Path)

    paragraphization_check = commands.add_parser("paragraphization-check", help="Validate a manual-spans artifact without registering it")
    paragraphization_check.add_argument("manifest", type=Path)
    paragraphization_check.add_argument("artifact", type=Path)
    paragraphization_check.add_argument("--chapter", type=int, required=True)
    paragraphization_check.add_argument("--root", type=Path, default=Path.cwd())

    paragraphization_register = commands.add_parser("paragraphization-register", help="Freeze a confirmed manual-spans artifact in source provenance")
    paragraphization_register.add_argument("manifest", type=Path)
    paragraphization_register.add_argument("artifact", type=Path)
    paragraphization_register.add_argument("--chapter", type=int, required=True)
    paragraphization_register.add_argument("--root", type=Path, default=Path.cwd())

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
                    paragraphization_mode=ParagraphizationMode(args.paragraphization_mode),
                ),
            )
            save_manifest(args.output, manifest)
            print(f"Source manifest created: {args.output}")
        elif args.command == "ingest-text":
            manifest = load_manifest(args.manifest)
            _, output = ingest_text_chapter(args.input, manifest, args.manifest, args.chapter, PathPolicy(args.root))
            print(f"Immutable raw chapter ingested: {output}")
        elif args.command == "epub-inspect":
            print(json.dumps(inspect_epub(args.book), ensure_ascii=False, indent=2))
        elif args.command == "ingest-epub":
            manifest = load_manifest(args.manifest)
            _, output = ingest_epub_package(args.book, manifest, args.manifest, args.chapter, PathPolicy(args.root))
            print(f"Immutable raw EPUB ingested: {output}")
        elif args.command == "epub-artifact-generate":
            from .normalize.epub_artifact import generate_artifact, save_artifact
            from .ingest.epub import load_epub_package
            from .ingest.chapter_map import load_registered_chapter_map
            manifest = load_manifest(args.manifest)
            package = load_epub_package(args.book)
            chapter_map = load_registered_chapter_map(manifest, PathPolicy(args.root), package) if manifest.chapter_map_artifact else None
            artifact = generate_artifact(args.book, manifest, args.chapter, args.toc_index, chapter_map)
            try:
                source = manifest.chapter(args.chapter)
                if source.sha256 != artifact.raw_epub_sha256:
                    raise ValueError("Proposal EPUB differs from the manifest's immutable package")
            except KeyError:
                pass
            save_artifact(args.output, artifact)
            print(f"EPUB paragraphization proposal created: {args.output} ({len(artifact.paragraphs)} paragraphs; status=draft)")
        elif args.command == "epub-artifact-check":
            from .ingest.epub import load_epub_package
            from .normalize.epub_artifact import load_artifact as load_epub_artifact, validate_artifact as validate_epub_artifact
            from .ingest.chapter_map import load_registered_chapter_map
            paths = PathPolicy(args.root)
            manifest = load_manifest(args.manifest)
            source = manifest.chapter(args.chapter)
            artifact = load_epub_artifact(args.artifact)
            package = load_epub_package(paths.resolve(source.raw_location))
            validate_epub_artifact(artifact, package, manifest, args.chapter, load_registered_chapter_map(manifest, paths, package))
            print(f"EPUB artifact is valid: {args.artifact} ({len(artifact.paragraphs)} paragraphs; status={artifact.status})")
        elif args.command == "epub-artifact-register":
            from .normalize.epub_artifact import register_artifact as register_epub_artifact
            manifest = load_manifest(args.manifest)
            updated = register_epub_artifact(manifest, args.manifest, args.artifact, args.chapter, PathPolicy(args.root))
            source = updated.chapter(args.chapter)
            print(f"EPUB artifact frozen: {source.paragraphization_artifact} sha256={source.paragraphization_sha256}")
        elif args.command == "chapter-map-generate":
            from .ingest.chapter_map import generate_chapter_map, save_chapter_map
            from .ingest.epub import load_epub_package
            paths = PathPolicy(args.root); manifest = load_manifest(args.manifest)
            package = load_epub_package(paths.resolve(manifest.chapters[0].raw_location))
            value = generate_chapter_map(manifest, package, args.canonical_start)
            save_chapter_map(args.output, value)
            print(f"Chapter Map proposal created: {args.output} ({len(value.entries)} navigation entries; status=draft)")
        elif args.command == "chapter-map-check":
            from .ingest.chapter_map import load_chapter_map, validate_chapter_map, chapter_map_summary
            from .ingest.epub import load_epub_package
            paths = PathPolicy(args.root); manifest = load_manifest(args.manifest); value = load_chapter_map(args.chapter_map)
            validate_chapter_map(value, manifest, load_epub_package(paths.resolve(manifest.chapters[0].raw_location)))
            print(json.dumps(chapter_map_summary(value), ensure_ascii=False, indent=2))
        elif args.command == "chapter-map-register":
            from .ingest.chapter_map import register_chapter_map
            updated = register_chapter_map(load_manifest(args.manifest), args.manifest, args.chapter_map, PathPolicy(args.root))
            print(f"Chapter Map frozen: {updated.chapter_map_artifact} sha256={updated.chapter_map_sha256}")
        elif args.command == "chapter-map-inspect":
            from .ingest.chapter_map import chapter_map_summary, load_registered_chapter_map
            from .ingest.epub import load_epub_package
            paths = PathPolicy(args.root); manifest = load_manifest(args.manifest)
            package = load_epub_package(paths.resolve(manifest.chapters[0].raw_location))
            value = load_registered_chapter_map(manifest, paths, package)
            if value is None: raise ValueError("Source Manifest has no registered Chapter Map")
            print(json.dumps(chapter_map_summary(value), ensure_ascii=False, indent=2))
        elif args.command == "normalize":
            paths = PathPolicy(args.root)
            manifest = load_manifest(args.manifest)
            chapter = normalize_manifest_chapter(manifest, args.manifest, args.chapter, paths)
            output = args.output or paths.normalized_chapter(chapter.source, args.chapter)
            save_chapter(output, chapter)
            print(f"Normalized chapter created: {output} ({len(chapter.paragraphs)} paragraphs)")
        elif args.command == "paragraphization-check":
            paths = PathPolicy(args.root)
            manifest = load_manifest(args.manifest)
            source = manifest.chapter(args.chapter)
            raw_text = paths.resolve(source.raw_location).read_text(encoding="utf-8")
            artifact = load_artifact(args.artifact)
            validate_artifact(artifact, raw_text, manifest, args.chapter, source.sha256)
            print(f"Paragraphization artifact is valid: {args.artifact} ({len(artifact.spans)} spans)")
        elif args.command == "paragraphization-register":
            manifest = load_manifest(args.manifest)
            updated = register_artifact(manifest, args.manifest, args.artifact, args.chapter, PathPolicy(args.root))
            source = updated.chapter(args.chapter)
            print(f"Paragraphization artifact registered: {source.paragraphization_artifact} sha256={source.paragraphization_sha256}")
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
