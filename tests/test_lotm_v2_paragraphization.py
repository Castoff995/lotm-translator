from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from src.lotm_v2.domain import (
    Language, ParagraphizationMode, SourceDescriptor, SourceFormat, SourceId,
    SourceChapter, SourceManifest, SourceRole,
)
from src.lotm_v2.infrastructure.corpus_io import load_chapter, load_manifest, save_chapter
from src.lotm_v2.infrastructure.paths import PathPolicy
from src.lotm_v2.ingest import ingest_text_chapter
from src.lotm_v2.normalize.paragraphization import (
    ExclusionReason, ParagraphSpan, ParagraphizationArtifact, SpanDisposition,
    register_artifact, save_artifact, validate_artifact,
)
from src.lotm_v2.normalize.service import normalize_manifest_chapter, normalize_text_chapter


def make_manifest(mode: ParagraphizationMode = ParagraphizationMode.MANUAL_SPANS) -> SourceManifest:
    return SourceManifest(
        "1.1",
        SourceDescriptor(
            SourceId("ru-official"), "lotm", Language.RU, SourceRole.OFFICIAL,
            "pilot", SourceFormat.OCR, "2.1-draft", mode,
        ),
    )


def make_artifact(raw: str, spans: tuple[ParagraphSpan, ...], status: str = "confirmed") -> ParagraphizationArtifact:
    return ParagraphizationArtifact(
        "1.0-draft", "chapter-1-v1", "lotm", SourceId("ru-official"), 1,
        ParagraphizationMode.MANUAL_SPANS, hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        len(raw.splitlines()), spans, status=status,
    )


class ParagraphizationTests(unittest.TestCase):
    def test_multiline_span_becomes_one_physical_paragraph(self) -> None:
        raw = "Первая строка абзаца\nпродолжение того же абзаца"
        artifact = make_artifact(raw, (ParagraphSpan(1, 2),))
        chapter = normalize_text_chapter(
            raw, make_manifest(), Path("manifest.json"), 1, "raw.txt",
            hashlib.sha256(raw.encode()).hexdigest(), artifact, "map.json", "b" * 64,
        )
        self.assertEqual(len(chapter.paragraphs), 1)
        self.assertEqual(chapter.paragraphs[0].raw_text, raw)
        self.assertEqual(chapter.paragraphs[0].normalized_text, "Первая строка абзаца продолжение того же абзаца")
        self.assertEqual((chapter.paragraphs[0].provenance.start_line, chapter.paragraphs[0].provenance.end_line), (1, 2))

    def test_multiple_spans_have_stable_ids(self) -> None:
        raw = "Первая\nпродолжение\nВторая"
        artifact = make_artifact(raw, (ParagraphSpan(1, 2), ParagraphSpan(3, 3)))
        arguments = (raw, make_manifest(), Path("manifest.json"), 1, "raw.txt", hashlib.sha256(raw.encode()).hexdigest(), artifact, "map.json", "b" * 64)
        first = normalize_text_chapter(*arguments)
        second = normalize_text_chapter(*arguments)
        self.assertEqual([str(item.id) for item in first.paragraphs], ["lotm:ru-official:0001:p000001", "lotm:ru-official:0001:p000002"])
        self.assertEqual([item.id for item in first.paragraphs], [item.id for item in second.paragraphs])

    def test_overlapping_spans_are_rejected(self) -> None:
        raw = "one\ntwo\nthree"
        artifact = make_artifact(raw, (ParagraphSpan(1, 2), ParagraphSpan(2, 3)))
        with self.assertRaises(ValueError):
            validate_artifact(artifact, raw, make_manifest(), 1, hashlib.sha256(raw.encode()).hexdigest())

    def test_reversed_span_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ParagraphSpan(3, 2)

    def test_lost_content_line_is_rejected(self) -> None:
        raw = "one\ntwo"
        artifact = make_artifact(raw, (ParagraphSpan(1, 1),))
        with self.assertRaisesRegex(ValueError, "loses content lines"):
            validate_artifact(artifact, raw, make_manifest(), 1, hashlib.sha256(raw.encode()).hexdigest())

    def test_explicit_exclusion_accounts_for_content(self) -> None:
        raw = "story\nOCR debris"
        artifact = make_artifact(raw, (
            ParagraphSpan(1, 1),
            ParagraphSpan(2, 2, SpanDisposition.EXCLUDED, ExclusionReason.OCR_ARTIFACT, "Verified against photo"),
        ))
        validate_artifact(artifact, raw, make_manifest(), 1, hashlib.sha256(raw.encode()).hexdigest())

    def test_provenance_round_trip_includes_artifact_and_lines(self) -> None:
        raw = "first\ncontinuation"
        artifact = make_artifact(raw, (ParagraphSpan(1, 2),))
        chapter = normalize_text_chapter(
            raw, make_manifest(), Path("manifest.json"), 1, "raw.txt",
            hashlib.sha256(raw.encode()).hexdigest(), artifact, "map.json", "b" * 64,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chapter.json"
            save_chapter(path, chapter)
            loaded = load_chapter(path)
        self.assertEqual(loaded, chapter)
        provenance = loaded.paragraphs[0].provenance
        self.assertEqual((provenance.start_line, provenance.end_line), (1, 2))
        self.assertEqual(provenance.paragraphization_sha256, "b" * 64)

    def test_registered_artifact_change_is_detected(self) -> None:
        raw = "first\nsecond"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = PathPolicy(root)
            incoming = root / "incoming.txt"
            incoming.write_text(raw, encoding="utf-8")
            manifest_path = root / "data" / "manifests" / "v2" / "ru-official.json"
            manifest, _ = ingest_text_chapter(incoming, make_manifest(), manifest_path, 1, paths)
            source = manifest.chapter(1)
            artifact = ParagraphizationArtifact(
                "1.0-draft", "chapter-1-v1", "lotm", SourceId("ru-official"), 1,
                ParagraphizationMode.MANUAL_SPANS, source.sha256, 2,
                (ParagraphSpan(1, 1), ParagraphSpan(2, 2)), status="confirmed",
            )
            artifact_path = root / "data" / "manifests" / "v2" / "paragraphization" / "ru-official" / "ch_0001.json"
            save_artifact(artifact_path, artifact)
            register_artifact(manifest, manifest_path, artifact_path, 1, paths)
            normalize_manifest_chapter(load_manifest(manifest_path), manifest_path, 1, paths)
            changed = ParagraphizationArtifact(**{**artifact.__dict__, "note": "changed after registration"})
            save_artifact(artifact_path, changed)
            with self.assertRaisesRegex(ValueError, "artifact changed"):
                normalize_manifest_chapter(load_manifest(manifest_path), manifest_path, 1, paths)

    def test_confirmed_artifact_cannot_be_replaced_for_same_source_revision(self) -> None:
        raw = "first\nsecond"
        manifest = make_manifest().with_chapter(
            SourceChapter(
                1, "data/raw/v2/ru-official/ch_0001.txt",
                hashlib.sha256(raw.encode()).hexdigest(),
            )
        )
        frozen = manifest.with_paragraphization(1, "map-v1.json", "a" * 64, "chapter-1-v1")
        with self.assertRaisesRegex(ValueError, "new source_id or source revision"):
            frozen.with_paragraphization(1, "map-v2.json", "b" * 64, "chapter-1-v2")

    def test_blank_lines_mode_continues_to_work(self) -> None:
        raw = "first line\nwrapped line\n\nsecond paragraph"
        manifest = make_manifest(ParagraphizationMode.BLANK_LINES)
        chapter = normalize_text_chapter(
            raw, manifest, Path("manifest.json"), 1, "raw.txt",
            hashlib.sha256(raw.encode()).hexdigest(),
        )
        self.assertEqual(len(chapter.paragraphs), 2)
        self.assertEqual(chapter.paragraphs[0].normalized_text, "first line wrapped line")
        self.assertEqual((chapter.paragraphs[1].provenance.start_line, chapter.paragraphs[1].provenance.end_line), (4, 4))


if __name__ == "__main__":
    unittest.main()
