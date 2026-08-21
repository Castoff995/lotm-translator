from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from src.lotm_v2.cli import main as cli_main
from src.lotm_v2.domain import (
    Chapter, ChapterId, Language, Paragraph, ParagraphId, ParagraphType,
    SourceDescriptor, SourceFormat, SourceId, SourceManifest, SourceRole,
)
from src.lotm_v2.gold.io import load_gold, save_gold
from src.lotm_v2.gold.model import (
    BoundaryDecision, GoldAlignmentSide, GoldAlignmentUnit, GoldBoundary,
    GoldChapter, GoldFlag, GoldGap, GoldSourceRef, GoldStatus, alignment_unit_id,
)
from src.lotm_v2.gold.validation import GoldValidationError, validate_gold_chapter
from src.lotm_v2.infrastructure.corpus_io import load_chapter, load_manifest, save_chapter, save_manifest
from src.lotm_v2.infrastructure.paths import PathPolicy
from src.lotm_v2.ingest import ingest_text_chapter
from src.lotm_v2.normalize.service import normalize_text_chapter


SHA = "a" * 64


def make_chapter(language: Language, source_name: str, count: int = 2) -> Chapter:
    source = SourceId(source_name)
    chapter_id = ChapterId("lotm", 1)
    paragraphs = tuple(
        Paragraph(
            id=ParagraphId("lotm", source, 1, index), source=source, language=language,
            chapter=chapter_id, index=index, raw_text=f"raw {source_name} {index}",
            normalized_text=f"text {source_name} {index}", paragraph_type=ParagraphType.STORY,
        )
        for index in range(1, count + 1)
    )
    return Chapter("2.0", chapter_id, source, language, paragraphs)


def source_ref(chapter: Chapter) -> GoldSourceRef:
    return GoldSourceRef(chapter.source, chapter.language, f"data/normalized/{chapter.source}.json", SHA)


def side(chapter: Chapter, index: int) -> GoldAlignmentSide:
    return GoldAlignmentSide(paragraphs=(chapter.paragraphs[index - 1].id,))


def valid_fixture() -> tuple[GoldChapter, tuple[Chapter, ...]]:
    zh, en, ru = make_chapter(Language.ZH, "zh"), make_chapter(Language.EN, "en"), make_chapter(Language.RU, "ru-official")
    chapter_id = zh.id
    units = tuple(
        GoldAlignmentUnit(alignment_unit_id(chapter_id, index), side(zh, index), side(en, index), side(ru, index))
        for index in (1, 2)
    )
    gold = GoldChapter(
        "1.0-draft", GoldStatus.DRAFT, chapter_id, tuple(source_ref(item) for item in (zh, en, ru)),
        units, (GoldBoundary(units[0].id, BoundaryDecision.JOIN),),
    )
    return gold, (zh, en, ru)


class FoundationTests(unittest.TestCase):
    def test_stable_paragraph_id(self) -> None:
        first = ParagraphId("lotm", SourceId("zh"), 1, 42)
        second = ParagraphId("lotm", SourceId("zh"), 1, 42)
        self.assertEqual(first, second)
        self.assertEqual(str(first), "lotm:zh:0001:p000042")
        self.assertEqual(ParagraphId.parse(str(first)), first)

    def test_manifest_and_chapter_round_trip(self) -> None:
        manifest = SourceManifest("1.0", SourceDescriptor(SourceId("zh"), "lotm", Language.ZH, SourceRole.ORIGINAL, "pilot", SourceFormat.TEXT, "2.0"))
        chapter = make_chapter(Language.ZH, "zh")
        with tempfile.TemporaryDirectory() as directory:
            manifest_path, chapter_path = Path(directory) / "manifest.json", Path(directory) / "chapter.json"
            save_manifest(manifest_path, manifest)
            save_chapter(chapter_path, chapter)
            self.assertEqual(load_manifest(manifest_path), manifest)
            self.assertEqual(load_chapter(chapter_path), chapter)

    def test_normalization_preserves_raw_text(self) -> None:
        manifest = SourceManifest("1.0", SourceDescriptor(SourceId("en"), "lotm", Language.EN, SourceRole.OFFICIAL, "pilot", SourceFormat.TEXT, "2.0"))
        raw = "First physical\nline.\n\nTranslator's note: keep me."
        chapter = normalize_text_chapter(raw, manifest, Path("manifest.json"), 1, "raw/ch1.txt", hashlib.sha256(raw.encode()).hexdigest())
        self.assertEqual(chapter.paragraphs[0].raw_text, "First physical\nline.")
        self.assertEqual(chapter.paragraphs[0].normalized_text, "First physical line.")
        self.assertEqual(chapter.paragraphs[1].paragraph_type, ParagraphType.TRANSLATOR_NOTE)
        self.assertIn("Translator's note", chapter.paragraphs[1].raw_text)

    def test_ingest_rejects_raw_overwrite(self) -> None:
        manifest = SourceManifest("1.0", SourceDescriptor(SourceId("zh"), "lotm", Language.ZH, SourceRole.ORIGINAL, "pilot", SourceFormat.TEXT, "2.0"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "incoming.txt"
            manifest_path = root / "manifest.json"
            source.write_text("original", encoding="utf-8")
            ingest_text_chapter(source, manifest, manifest_path, 1, PathPolicy(root))
            source.write_text("changed", encoding="utf-8")
            with self.assertRaises(ValueError):
                ingest_text_chapter(source, load_manifest(manifest_path), manifest_path, 1, PathPolicy(root))

    def test_pilot_cli_creates_draft_without_alignment(self) -> None:
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            root = Path(directory)
            normalized: list[Path] = []
            specifications = (("zh", "zh", "original"), ("en", "en", "official"), ("ru-official", "ru", "official"))
            for source_id, language, role in specifications:
                input_path = root / f"{source_id}.txt"
                input_path.write_text(f"{source_id} paragraph one.\n\n{source_id} paragraph two.", encoding="utf-8")
                manifest_path = root / "data" / "manifests" / "v2" / f"{source_id}.json"
                self.assertEqual(cli_main(["manifest-init", str(manifest_path), "--source-id", source_id, "--language", language, "--role", role, "--edition", "pilot", "--format", "text"]), 0)
                self.assertEqual(cli_main(["ingest-text", str(manifest_path), str(input_path), "--chapter", "1", "--root", str(root)]), 0)
                self.assertEqual(cli_main(["normalize", str(manifest_path), "--chapter", "1", "--root", str(root)]), 0)
                normalized.append(root / "data" / "normalized" / "v2" / source_id / "ch_0001.json")
            self.assertEqual(cli_main(["gold-draft", *(str(path) for path in normalized), "--root", str(root)]), 0)
            draft = load_gold(root / "data" / "gold" / "v2" / "ch_0001.json")
            self.assertEqual(draft.status, GoldStatus.DRAFT)
            self.assertEqual(draft.alignment_units, ())


class GoldValidationTests(unittest.TestCase):
    def test_gold_serialization_round_trip_and_valid_chapter(self) -> None:
        gold, chapters = valid_fixture()
        validate_gold_chapter(gold, chapters)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gold.json"
            save_gold(path, gold)
            self.assertEqual(load_gold(path), gold)

    def test_duplicate_paragraph_is_rejected(self) -> None:
        gold, chapters = valid_fixture()
        first, second = gold.alignment_units
        duplicate = GoldAlignmentUnit(second.id, first.zh, second.en, second.ru)
        broken = GoldChapter(gold.schema_version, gold.status, gold.chapter, gold.sources, (first, duplicate), gold.boundaries)
        with self.assertRaises(GoldValidationError):
            validate_gold_chapter(broken, chapters)

    def test_missing_paragraph_is_rejected(self) -> None:
        gold, chapters = valid_fixture()
        broken = GoldChapter(gold.schema_version, gold.status, gold.chapter, gold.sources, (gold.alignment_units[0],), ())
        with self.assertRaises(GoldValidationError):
            validate_gold_chapter(broken, chapters)

    def test_invalid_reference_is_rejected(self) -> None:
        gold, chapters = valid_fixture()
        first = gold.alignment_units[0]
        invalid = GoldAlignmentSide(paragraphs=(ParagraphId("lotm", SourceId("zh"), 1, 999),))
        unit = GoldAlignmentUnit(first.id, invalid, first.en, first.ru)
        broken = GoldChapter(gold.schema_version, gold.status, gold.chapter, gold.sources, (unit,), ())
        with self.assertRaises(GoldValidationError):
            validate_gold_chapter(broken, chapters)

    def test_non_monotonic_alignment_is_rejected(self) -> None:
        gold, chapters = valid_fixture()
        zh, en, ru = chapters
        units = (
            GoldAlignmentUnit(alignment_unit_id(gold.chapter, 1), side(zh, 2), side(en, 1), side(ru, 1)),
            GoldAlignmentUnit(alignment_unit_id(gold.chapter, 2), side(zh, 1), side(en, 2), side(ru, 2)),
        )
        broken = GoldChapter(gold.schema_version, gold.status, gold.chapter, gold.sources, units, (GoldBoundary(units[0].id, BoundaryDecision.BREAK),))
        with self.assertRaises(GoldValidationError):
            validate_gold_chapter(broken, chapters)

    def test_missing_boundary_is_rejected(self) -> None:
        gold, chapters = valid_fixture()
        broken = GoldChapter(gold.schema_version, gold.status, gold.chapter, gold.sources, gold.alignment_units, ())
        with self.assertRaises(GoldValidationError):
            validate_gold_chapter(broken, chapters)

    def test_explicit_gap_is_valid(self) -> None:
        zh, en, ru = make_chapter(Language.ZH, "zh", 1), make_chapter(Language.EN, "en", 1), make_chapter(Language.RU, "ru-official", 0)
        unit = GoldAlignmentUnit(
            alignment_unit_id(zh.id, 1), side(zh, 1), side(en, 1),
            GoldAlignmentSide(gap=GoldGap(GoldFlag.OMISSION, "No Russian counterpart")),
            flags=(GoldFlag.OMISSION,),
        )
        gold = GoldChapter("1.0-draft", GoldStatus.DRAFT, zh.id, tuple(source_ref(item) for item in (zh, en, ru)), (unit,), ())
        validate_gold_chapter(gold, (zh, en, ru))


if __name__ == "__main__":
    unittest.main()
