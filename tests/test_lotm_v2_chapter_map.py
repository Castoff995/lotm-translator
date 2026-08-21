from __future__ import annotations

import ast
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from src.lotm_v2 import SOURCE_MANIFEST_SCHEMA_VERSION
from src.lotm_v2.domain import (
    ChapterId, Language, ParagraphizationMode, SourceChapter, SourceDescriptor,
    SourceFormat, SourceId, SourceManifest, SourceRole,
)
from src.lotm_v2.gold.io import load_gold
from src.lotm_v2.infrastructure.corpus_io import load_chapter, load_manifest, save_manifest
from src.lotm_v2.infrastructure.paths import PathPolicy
from src.lotm_v2.ingest.chapter_map import (
    ChapterMapEntryKind, SourceChapterMap, chapter_map_from_dict, chapter_map_sha256,
    chapter_map_to_dict, generate_chapter_map, load_chapter_map, parse_chapter_label,
    parse_chinese_number, register_chapter_map, save_chapter_map, validate_chapter_map,
)
from src.lotm_v2.ingest.epub import EpubPackage, NavigationEntry, load_epub_package
from src.lotm_v2.normalize.epub_artifact import generate_artifact, load_artifact, validate_artifact

from test_lotm_v2_epub import make_epub


SHA = "a" * 64


def nav(labels: list[str]) -> tuple[NavigationEntry, ...]:
    return tuple(NavigationEntry(label, f"EPUB/{index}.xhtml", None, 0) for index, label in enumerate(labels))


def package(labels: list[str], sha: str = SHA) -> EpubPackage:
    return EpubPackage(Path("synthetic.epub"), sha, "3.0", "EPUB/content.opf", "Synthetic", (), nav(labels), True)


def manifest(sha: str = SHA) -> SourceManifest:
    return SourceManifest(
        SOURCE_MANIFEST_SCHEMA_VERSION,
        SourceDescriptor(SourceId("zh"), "lotm", Language.ZH, SourceRole.ORIGINAL,
                         "synthetic", SourceFormat.EPUB, "2.2-draft", ParagraphizationMode.EPUB_STRUCTURE),
        (SourceChapter(1, "data/raw/v2/zh/source.epub", sha),),
    )


class ChapterMapTests(unittest.TestCase):
    def test_chapter_id_remains_canonical(self) -> None:
        self.assertEqual(str(ChapterId("lotm", 214)), "lotm:0214")

    def test_chinese_and_arabic_numeral_parser(self) -> None:
        self.assertEqual(parse_chinese_number("一"), 1)
        self.assertEqual(parse_chinese_number("十"), 10)
        self.assertEqual(parse_chinese_number("二百一十三"), 213)
        self.assertEqual(parse_chapter_label("第214章 Example")[0], 214)
        self.assertEqual(parse_chapter_label("Chapter 501: Example")[0], 501)

    def test_one_scope_source_without_reset(self) -> None:
        value = generate_chapter_map(manifest(), package(["Chapter 1", "Chapter 2", "Chapter 3"]))
        chapters = [item for item in value.entries if item.kind is ChapterMapEntryKind.CHAPTER]
        self.assertEqual([item.numbering_scope for item in chapters], [1, 1, 1])
        self.assertEqual([item.canonical_chapter for item in chapters], [1, 2, 3])

    def test_reset_ancillary_and_complete_classification(self) -> None:
        value = generate_chapter_map(manifest(), package(["Chapter 1", "Chapter 2", "Summary", "Chapter 1", "Chapter 2"]))
        self.assertEqual([item.kind.value for item in value.entries], ["chapter", "chapter", "ancillary", "chapter", "chapter"])
        chapters = [item for item in value.entries if item.kind is ChapterMapEntryKind.CHAPTER]
        self.assertEqual([item.numbering_scope for item in chapters], [1, 1, 2, 2])
        self.assertEqual([item.canonical_chapter for item in chapters], [1, 2, 3, 4])
        validate_chapter_map(value, manifest(), package(["Chapter 1", "Chapter 2", "Summary", "Chapter 1", "Chapter 2"]))

    def test_canonical_offset_and_incomplete_range_are_supported(self) -> None:
        value = generate_chapter_map(manifest(), package(["Chapter 1", "Chapter 2"]), 501)
        self.assertEqual([value.chapter(i).local_chapter_number for i in (501, 502)], [1, 2])

    def test_unparseable_numbered_looking_entry_is_ambiguous(self) -> None:
        value = generate_chapter_map(manifest(), package(["第甲章 Unknown"]))
        self.assertEqual(value.entries[0].kind, ChapterMapEntryKind.AMBIGUOUS)
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            validate_chapter_map(replace(value, status="confirmed"), manifest(), package(["第甲章 Unknown"]))

    def test_duplicate_toc_index_rejected(self) -> None:
        value = generate_chapter_map(manifest(), package(["Chapter 1", "Chapter 2"]))
        duplicate = replace(value.entries[1], toc_index=0)
        with self.assertRaisesRegex(ValueError, "toc_index"):
            validate_chapter_map(replace(value, entries=(value.entries[0], duplicate)), manifest(), package(["Chapter 1", "Chapter 2"]))

    def test_duplicate_canonical_and_local_identity_rejected(self) -> None:
        value = generate_chapter_map(manifest(), package(["Chapter 1", "Chapter 2"]))
        with self.assertRaisesRegex(ValueError, "Canonical"):
            validate_chapter_map(replace(value, entries=(value.entries[0], replace(value.entries[1], canonical_chapter=1))), manifest(), package(["Chapter 1", "Chapter 2"]))
        with self.assertRaisesRegex(ValueError, "source-local"):
            validate_chapter_map(replace(value, entries=(value.entries[0], replace(value.entries[1], local_chapter_number=1))), manifest(), package(["Chapter 1", "Chapter 2"]))

    def test_source_chapter_ordinal_must_be_contiguous(self) -> None:
        value = generate_chapter_map(manifest(), package(["Chapter 1", "Chapter 2"]))
        with self.assertRaisesRegex(ValueError, "ordinal"):
            validate_chapter_map(replace(value, entries=(value.entries[0], replace(value.entries[1], source_chapter_ordinal=3))), manifest(), package(["Chapter 1", "Chapter 2"]))

    def test_wrong_raw_hash_and_navigation_href_rejected(self) -> None:
        value = generate_chapter_map(manifest(), package(["Chapter 1"]))
        with self.assertRaisesRegex(ValueError, "raw source provenance"):
            validate_chapter_map(replace(value, raw_source_sha256="b" * 64), manifest(), package(["Chapter 1"]))
        with self.assertRaisesRegex(ValueError, "locator mismatch"):
            validate_chapter_map(replace(value, entries=(replace(value.entries[0], toc_href="wrong.xhtml"),)), manifest(), package(["Chapter 1"]))

    def test_map_round_trip(self) -> None:
        value = generate_chapter_map(manifest(), package(["Chapter 1", "Summary", "Chapter 1"]))
        self.assertEqual(chapter_map_from_dict(chapter_map_to_dict(value)), value)

    def test_map_hash_registration_and_replacement_guard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); paths = PathPolicy(root)
            book = make_epub(root / "book.epub")
            epub = load_epub_package(book)
            source_manifest = SourceManifest(
                SOURCE_MANIFEST_SCHEMA_VERSION,
                SourceDescriptor(SourceId("zh"), "lotm", Language.ZH, SourceRole.ORIGINAL, "synthetic", SourceFormat.EPUB, "2.2-draft", ParagraphizationMode.EPUB_STRUCTURE),
                (SourceChapter(1, paths.relative(book), epub.package_sha256),),
            )
            manifest_path = paths.manifest(SourceId("zh")); save_manifest(manifest_path, source_manifest)
            map_path = paths.chapter_map(SourceId("zh")); save_chapter_map(map_path, generate_chapter_map(source_manifest, epub))
            updated = register_chapter_map(source_manifest, manifest_path, map_path, paths)
            self.assertEqual(updated.chapter_map_sha256, chapter_map_sha256(map_path))
            self.assertEqual(load_chapter_map(map_path).status, "confirmed")
            with self.assertRaisesRegex(ValueError, "cannot be replaced"):
                updated.with_chapter_map("other.json", "b" * 64, "2")

    def test_real_zh_canonical_lookup_reset_points_and_chapter1(self) -> None:
        root = Path(__file__).resolve().parents[1]
        value = load_chapter_map(root / "data/manifests/v2/chapter-maps/zh.json")
        self.assertEqual((value.chapter(1).toc_index, value.chapter(1).numbering_scope, value.chapter(1).local_chapter_number), (0, 1, 1))
        self.assertEqual((value.chapter(214).toc_index, value.chapter(214).numbering_scope, value.chapter(214).local_chapter_number), (217, 2, 1))
        self.assertEqual((value.chapter(483).toc_index, value.chapter(483).numbering_scope, value.chapter(483).local_chapter_number), (494, 3, 1))

    def test_registered_map_rejects_conflicting_toc_index_and_validates_existing_artifact(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source_manifest = load_manifest(root / "data/manifests/v2/zh.json")
        value = load_chapter_map(root / source_manifest.chapter_map_artifact)
        book = root / source_manifest.chapter(1).raw_location
        with self.assertRaisesRegex(ValueError, "conflicts"):
            generate_artifact(book, source_manifest, 1, 217, value)
        artifact = load_artifact(root / source_manifest.chapter(1).paragraphization_artifact)
        validate_artifact(artifact, load_epub_package(book), source_manifest, 1, value)

    def test_existing_pilot_ids_hashes_and_gold_are_unchanged(self) -> None:
        root = Path(__file__).resolve().parents[1]
        expected = {
            "zh": (70, "ac3bd992e582783a335a97dbca5493eb78b1d03606d2823e3b949f6cc9093858"),
            "en": (71, "789d93fed725fea45a78578911e1185f42c50c74fe963c5448be13055decd5b3"),
            "ru-official": (54, "bc7f4234d0653014146f061f6eef9f623b450bfb01bcc86d751771823d46f03c"),
        }
        for source, (count, digest) in expected.items():
            path = root / f"data/normalized/v2/{source}/ch_0001.json"
            chapter = load_chapter(path)
            self.assertEqual((len(chapter.paragraphs), hashlib.sha256(path.read_bytes()).hexdigest()), (count, digest))
            self.assertEqual([str(item.id) for item in chapter.paragraphs], [f"lotm:{source}:0001:p{index:06d}" for index in range(1, count + 1)])
        gold = load_gold(root / "data/gold/v2/ch_0001.json")
        self.assertEqual((len(gold.alignment_units), len(gold.paragraph_dispositions), len(gold.boundaries)), (0, 0, 0))

    def test_schemas_parse_and_no_later_phase_dependencies(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for path in (root / "schemas/v2").glob("*.json"):
            self.assertIsInstance(json.loads(path.read_text(encoding="utf-8")), dict)
        forbidden = {"alignment", "hints", "gold"}
        for path in (root / "src/lotm_v2/ingest/chapter_map.py", root / "src/lotm_v2/normalize/epub_artifact.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
            imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
            self.assertFalse(any(set(name.lower().split(".")) & forbidden for name in imports))


if __name__ == "__main__":
    unittest.main()
