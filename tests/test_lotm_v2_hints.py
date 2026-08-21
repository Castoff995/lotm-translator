from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.lotm_v2.glossary import GlossaryDuplicateError, GlossaryService
from src.lotm_v2.hints.model import HintToken, LexicalAlignment
from src.lotm_v2.hints.providers import HintUnavailable, JiebaChineseSegmenter
from src.lotm_v2.hints.selection import select_contiguous
from src.lotm_v2.hints.service import HintService, bundle_to_dict


class TranslationFake:
    def __init__(self, provider_id: str = "fake-translation:v1") -> None:
        self.provider_id = provider_id
        self.calls = 0

    def translate(self, text: str, source_language: str, target_language: str) -> str:
        self.calls += 1
        return "Klein became a Beyonder."


class SegmenterFake:
    provider_id = "fake-segmenter:v1"

    def segment(self, text: str) -> tuple[HintToken, ...]:
        return (
            HintToken(0, "克莱恩", 0, 3), HintToken(1, "成为", 3, 5),
            HintToken(2, "非凡者", 5, 8),
        )


class AlignmentFake:
    provider_id = "fake-aligner:v1"

    def align(self, zh_text, en_text, zh_tokens, en_tokens):
        return (LexicalAlignment(2, 3, 3, 4, 0.91),)


class UnavailableTranslation(TranslationFake):
    def translate(self, text: str, source_language: str, target_language: str) -> str:
        raise HintUnavailable("synthetic model missing")


class UnavailableSegmenter(SegmenterFake):
    def segment(self, text: str) -> tuple[HintToken, ...]:
        raise HintUnavailable("synthetic segmenter missing")


class HintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def service(self, translation=None, segmentation=None) -> HintService:
        return HintService(
            self.root / "cache", translation or TranslationFake(),
            segmentation or SegmenterFake(), AlignmentFake(),
        )

    def test_jieba_segmentation_has_exact_offsets_and_multicharacter_token(self) -> None:
        text = "克莱恩成为了一名非凡者"
        tokens = JiebaChineseSegmenter().segment(text)
        self.assertTrue(any(len(item.surface) > 1 for item in tokens))
        for item in tokens:
            self.assertEqual(text[item.start_offset:item.end_offset], item.surface)

    def test_multi_token_selection_is_contiguous_and_exact(self) -> None:
        tokens = SegmenterFake().segment("克莱恩成为非凡者")
        selected = select_contiguous(tokens, 1, 3)
        self.assertEqual(selected["surface"], "成为非凡者")
        self.assertEqual((selected["start_offset"], selected["end_offset"]), (3, 8))
        with self.assertRaises(ValueError):
            select_contiguous(tokens, 2, 2)

    def test_translation_alignment_cache_and_hover_serialization(self) -> None:
        translation = TranslationFake()
        service = self.service(translation)
        first = bundle_to_dict(service.get("p1", "克莱恩成为非凡者"))
        second = bundle_to_dict(service.get("p1", "克莱恩成为非凡者"))
        self.assertEqual(translation.calls, 1)
        self.assertEqual(first, second)
        self.assertEqual(first["alignments"][0], {
            "zh_token_start": 2, "zh_token_end": 3,
            "en_token_start": 3, "en_token_end": 4, "score": 0.91,
        })

    def test_cache_invalidates_for_text_and_provider_version(self) -> None:
        first = TranslationFake("fake:v1")
        service = self.service(first)
        service.get("p1", "克莱恩成为非凡者")
        service.get("p2", "克莱恩成为了非凡者")
        self.assertEqual(first.calls, 2)
        second = TranslationFake("fake:v2")
        HintService(self.root / "cache", second, SegmenterFake(), AlignmentFake()).get(
            "p1", "克莱恩成为非凡者",
        )
        self.assertEqual(second.calls, 1)

    def test_missing_models_return_graceful_non_gold_payload(self) -> None:
        translation = bundle_to_dict(self.service(UnavailableTranslation()).get("p1", "克莱恩成为非凡者"))
        self.assertFalse(translation["available"])
        self.assertIn("synthetic model missing", translation["translation_error"])
        segmentation = bundle_to_dict(self.service(segmentation=UnavailableSegmenter()).get("p1", "克莱恩"))
        self.assertFalse(segmentation["available"])
        self.assertIn("synthetic segmenter missing", segmentation["segmentation_error"])

    def test_glossary_explicit_add_duplicate_needs_ru_and_lookup(self) -> None:
        service = GlossaryService(self.root / "glossary.json", "synthetic-work")
        self.assertFalse(service.path.exists())
        entry = service.add("非凡者", "Beyonder", 1, "zh:p1", 5, 8)
        self.assertEqual(entry.status.value, "needs_ru")
        self.assertIsNone(entry.ru_term)
        self.assertEqual(service.find("非凡者"), entry)
        self.assertEqual(service.occurrences("他是一名非凡者")[0]["matched_form"], "非凡者")
        with self.assertRaises(GlossaryDuplicateError):
            service.add("非凡者", "Extraordinary person", 1, "zh:p2", 0, 3)
        service.add_alias("非凡者", "zh", "超凡者")
        self.assertIsNotNone(service.find("超凡者"))


if __name__ == "__main__":
    unittest.main()
