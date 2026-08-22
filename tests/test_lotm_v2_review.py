from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from src.lotm_v2 import GOLD_SCHEMA_VERSION
from src.lotm_v2.domain import Chapter, ChapterId, Language, Paragraph, ParagraphId, ParagraphType, SourceId
from src.lotm_v2.gold.io import load_gold, save_gold
from src.lotm_v2.glossary import GlossaryService
from src.lotm_v2.hints import HintService, HintToken, LexicalAlignment
from src.lotm_v2.gold.model import (
    GoldAlignmentSide, GoldAlignmentUnit, GoldChapter, GoldSourceRef, GoldStatus, alignment_unit_id,
)
from src.lotm_v2.infrastructure.corpus_io import save_chapter
from src.lotm_v2.infrastructure.json_io import write_json_atomic
from src.lotm_v2.review import ReviewError, ReviewWorkspace
from src.lotm_v2.review.server import ReviewHTTPServer


def chapter(language: Language, source_name: str, count: int, *, heading: bool = False) -> Chapter:
    source = SourceId(source_name)
    chapter_id = ChapterId("synthetic-work", 7)
    paragraphs = tuple(
        Paragraph(
            ParagraphId(chapter_id.work_id, source, chapter_id.number, index), source, language,
            chapter_id, index, f"raw {source_name} {index}", f"text {source_name} {index}",
            ParagraphType.HEADING if heading and index == 1 else ParagraphType.STORY,
        )
        for index in range(1, count + 1)
    )
    return Chapter("2.1-draft", chapter_id, source, language, paragraphs)


class Fixture:
    def __init__(self, root: Path, counts: tuple[int, int, int] = (3, 3, 3), *, heading: bool = False) -> None:
        self.root = root
        self.gold_path = root / "data" / "gold" / "v2" / "ch_0007.json"
        self.chapters = tuple(
            chapter(language, source, count, heading=heading)
            for language, source, count in zip(
                (Language.ZH, Language.EN, Language.RU), ("zh-test", "en-test", "ru-test"), counts,
            )
        )
        self.source_paths: list[Path] = []
        refs: list[GoldSourceRef] = []
        for item in self.chapters:
            path = root / "data" / "normalized" / "v2" / str(item.source) / "ch_0007.json"
            save_chapter(path, item)
            self.source_paths.append(path)
            refs.append(GoldSourceRef(
                item.source, item.language, path.relative_to(root).as_posix(),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            ))
        save_gold(self.gold_path, GoldChapter(
            GOLD_SCHEMA_VERSION, GoldStatus.DRAFT, self.chapters[0].id, tuple(refs),
        ))


def selection(zh: int = 1, en: int = 1, ru: int = 1) -> dict[str, object]:
    return {"sides": {"zh": {"count": zh}, "en": {"count": en}, "ru": {"count": ru}}}


class ReviewSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_dynamic_counts_and_one_to_one_confirmation(self) -> None:
        fixture = Fixture(self.root, (2, 3, 4))
        session = ReviewWorkspace(fixture.gold_path, self.root)
        snapshot = session.snapshot()
        self.assertEqual(snapshot["progress"]["totals"], {"zh": 2, "en": 3, "ru": 4})
        result = session.confirm(selection())
        self.assertEqual(result["progress"]["cursors"], {"zh": 1, "en": 1, "ru": 1})

    def test_many_to_many_and_resume_reconstructs_cursors(self) -> None:
        fixture = Fixture(self.root)
        session = ReviewWorkspace(fixture.gold_path, self.root)
        preview = session.preview(selection(2, 2, 2))
        self.assertEqual(len(preview["selected"]["en"]), 2)
        session.confirm(selection(2, 2, 2))
        reopened = ReviewWorkspace(fixture.gold_path, self.root)
        self.assertEqual(reopened.snapshot()["progress"]["cursors"], {"zh": 2, "en": 2, "ru": 2})

    def test_explicit_gap_advances_only_non_gap_sources(self) -> None:
        fixture = Fixture(self.root, (1, 2, 1))
        session = ReviewWorkspace(fixture.gold_path, self.root)
        session.confirm(selection())
        result = session.confirm({"sides": {
            "zh": {"gap": True, "reason": "addition", "note": "EN-only material"},
            "en": {"count": 1},
            "ru": {"gap": True, "reason": "omission"},
        }})
        self.assertEqual(result["progress"]["cursors"], {"zh": 1, "en": 2, "ru": 1})
        self.assertIn("addition", result["units"][1]["flags"])
        self.assertEqual(result["progress"]["dispositions"], 0)

    def test_disposition_advances_only_affected_source(self) -> None:
        fixture = Fixture(self.root, (2, 2, 2))
        session = ReviewWorkspace(fixture.gold_path, self.root)
        result = session.disposition("ru", "metadata", "Synthetic legal line")
        self.assertEqual(result["progress"]["cursors"], {"zh": 0, "en": 0, "ru": 1})
        self.assertEqual(result["progress"]["dispositions"], 1)
        self.assertEqual(result["dispositions"][0]["paragraph"]["index"], 1)

    def test_undo_disposition_restores_source_cursor(self) -> None:
        fixture = Fixture(self.root)
        session = ReviewWorkspace(fixture.gold_path, self.root)
        session.disposition("en", "separator")
        result = session.undo()
        self.assertEqual(result["progress"]["cursors"], {"zh": 0, "en": 0, "ru": 0})
        self.assertEqual(result["dispositions"], [])

    def test_rollback_removes_only_downstream_dispositions(self) -> None:
        fixture = Fixture(self.root)
        session = ReviewWorkspace(fixture.gold_path, self.root)
        session.disposition("zh", "heading")  # before alignment unit 1
        session.confirm(selection())
        session.disposition("en", "publisher_note")  # after alignment unit 1
        result = session.rollback(1)
        self.assertEqual(result["progress"]["units"], 1)
        self.assertEqual(result["progress"]["dispositions"], 1)
        self.assertEqual(result["dispositions"][0]["reason"], "heading")
        self.assertEqual(result["progress"]["cursors"], {"zh": 2, "en": 1, "ru": 1})
        result = session.rollback(0)
        self.assertEqual(result["progress"]["cursors"], {"zh": 0, "en": 0, "ru": 0})

    def test_resume_reconstructs_mixed_unit_and_disposition_prefix(self) -> None:
        fixture = Fixture(self.root)
        session = ReviewWorkspace(fixture.gold_path, self.root)
        session.disposition("zh", "metadata")
        session.confirm(selection())
        session.disposition("ru", "footnote")
        reopened = ReviewWorkspace(fixture.gold_path, self.root)
        self.assertEqual(reopened.snapshot()["progress"]["cursors"], {"zh": 2, "en": 1, "ru": 2})
        self.assertEqual(reopened.snapshot()["progress"]["dispositions"], 2)

    def test_hash_mismatch_blocks_opening(self) -> None:
        fixture = Fixture(self.root)
        fixture.source_paths[0].write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ReviewError, "source mismatch"):
            ReviewWorkspace(fixture.gold_path, self.root)

    def test_unknown_schema_is_rejected(self) -> None:
        fixture = Fixture(self.root)
        gold = load_gold(fixture.gold_path)
        save_gold(fixture.gold_path, replace(gold, schema_version="99.0"))
        with self.assertRaisesRegex(ReviewError, "Unsupported Gold schema"):
            ReviewWorkspace(fixture.gold_path, self.root)

    def test_partial_validation_rejects_duplicate_and_non_monotonic_use(self) -> None:
        fixture = Fixture(self.root)
        gold = load_gold(fixture.gold_path)
        zh, en, ru = fixture.chapters
        first = GoldAlignmentUnit(
            alignment_unit_id(gold.chapter, 1),
            GoldAlignmentSide((zh.paragraphs[1].id,)),
            GoldAlignmentSide((en.paragraphs[0].id,)),
            GoldAlignmentSide((ru.paragraphs[0].id,)),
        )
        save_gold(fixture.gold_path, replace(gold, alignment_units=(first,)))
        with self.assertRaisesRegex(ReviewError, "next contiguous zh"):
            ReviewWorkspace(fixture.gold_path, self.root)

    def test_partial_validation_reports_duplicate_reference(self) -> None:
        fixture = Fixture(self.root)
        gold = load_gold(fixture.gold_path)
        zh, en, ru = fixture.chapters
        units = (
            GoldAlignmentUnit(
                alignment_unit_id(gold.chapter, 1),
                GoldAlignmentSide((zh.paragraphs[0].id,)),
                GoldAlignmentSide((en.paragraphs[0].id,)),
                GoldAlignmentSide((ru.paragraphs[0].id,)),
            ),
            GoldAlignmentUnit(
                alignment_unit_id(gold.chapter, 2),
                GoldAlignmentSide((zh.paragraphs[0].id,)),
                GoldAlignmentSide((en.paragraphs[1].id,)),
                GoldAlignmentSide((ru.paragraphs[1].id,)),
            ),
        )
        save_gold(fixture.gold_path, replace(gold, alignment_units=units))
        with self.assertRaisesRegex(ReviewError, "multiple Gold fates"):
            ReviewWorkspace(fixture.gold_path, self.root)

    def test_undo_and_rollback_remove_downstream_boundaries(self) -> None:
        fixture = Fixture(self.root)
        session = ReviewWorkspace(fixture.gold_path, self.root)
        for _ in range(3):
            session.confirm(selection())
        session.set_boundary(session.gold.alignment_units[0].id, "JOIN")
        session.set_boundary(session.gold.alignment_units[1].id, "BREAK")
        result = session.undo()
        self.assertEqual(result["progress"]["units"], 2)
        self.assertEqual(result["progress"]["boundaries_reviewed"], 1)
        result = session.rollback(0)
        self.assertEqual(result["progress"]["cursors"], {"zh": 0, "en": 0, "ru": 0})

    def test_join_break_replace_and_persist(self) -> None:
        fixture = Fixture(self.root, (2, 2, 2))
        session = ReviewWorkspace(fixture.gold_path, self.root)
        session.confirm(selection())
        session.confirm(selection())
        after = session.gold.alignment_units[0].id
        session.set_boundary(after, "JOIN", "same semantic unit")
        result = session.set_boundary(after, "BREAK")
        self.assertEqual(result["boundaries"], [{"after": after, "decision": "BREAK", "note": None}])
        self.assertEqual(ReviewWorkspace(fixture.gold_path, self.root).snapshot()["boundaries"][0]["decision"], "BREAK")

    def test_finish_requires_all_physical_paragraphs_and_boundaries(self) -> None:
        fixture = Fixture(self.root, (2, 2, 2), heading=True)
        session = ReviewWorkspace(fixture.gold_path, self.root)
        session.confirm(selection())
        with self.assertRaisesRegex(ReviewError, "Missing Gold fate"):
            session.finish()
        session.confirm(selection())
        with self.assertRaisesRegex(ReviewError, "Missing JOIN/BREAK"):
            session.finish()
        session.set_boundary(session.gold.alignment_units[0].id, "JOIN")
        result = session.finish()
        self.assertTrue(result["valid"])
        self.assertEqual(session.gold.status, GoldStatus.DRAFT)

    def test_confirmed_gold_is_read_only(self) -> None:
        fixture = Fixture(self.root)
        gold = load_gold(fixture.gold_path)
        save_gold(fixture.gold_path, replace(gold, status=GoldStatus.CONFIRMED))
        session = ReviewWorkspace(fixture.gold_path, self.root)
        self.assertTrue(session.snapshot()["read_only"])
        with self.assertRaisesRegex(ReviewError, "read-only"):
            session.confirm(selection())

    def test_atomic_writer_replaces_document_without_temp_residue(self) -> None:
        path = self.root / "nested" / "gold.json"
        write_json_atomic(path, {"version": 1})
        write_json_atomic(path, {"version": 2, "text": "synthetic"})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["version"], 2)
        self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_local_http_ui_and_preview_api(self) -> None:
        fixture = Fixture(self.root)
        session = ReviewWorkspace(fixture.gold_path, self.root)
        server = ReviewHTTPServer(("127.0.0.1", 0), session)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            html = urlopen(base + "/", timeout=3).read().decode("utf-8")
            self.assertIn("Human Gold Review Tool", html)
            snapshot = json.loads(urlopen(base + "/api/session", timeout=3).read())
            self.assertEqual(snapshot["progress"]["totals"], {"zh": 3, "en": 3, "ru": 3})
            body = json.dumps(selection()).encode("utf-8")
            request = Request(
                base + "/api/preview", data=body, method="POST",
                headers={"Content-Type": "application/json", "X-Review-Token": server.review_token},
            )
            preview = json.loads(urlopen(request, timeout=3).read())
            self.assertEqual(preview["id"], "synthetic-work:0007:a000001")
            self.assertEqual(load_gold(fixture.gold_path).alignment_units, ())
            disposition_request = Request(
                base + "/api/disposition",
                data=json.dumps({"language": "en", "reason": "metadata", "note": "Synthetic"}).encode("utf-8"),
                method="POST",
                headers={"Content-Type": "application/json", "X-Review-Token": server.review_token},
            )
            disposition_result = json.loads(urlopen(disposition_request, timeout=3).read())
            self.assertEqual(disposition_result["progress"]["cursors"], {"zh": 0, "en": 1, "ru": 0})
            self.assertEqual(len(session.gold.paragraph_dispositions), 1)
            self.assertEqual(len(load_gold(fixture.gold_path).paragraph_dispositions), 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_http_preview_and_confirm_preserve_three_three_one_counts(self) -> None:
        fixture = Fixture(self.root, (4, 4, 2))
        session = ReviewWorkspace(fixture.gold_path, self.root)
        server = ReviewHTTPServer(("127.0.0.1", 0), session)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        headers = {"Content-Type": "application/json", "X-Review-Token": server.review_token}
        original_gold = fixture.gold_path.read_bytes()
        body = json.dumps(selection(3, 3, 1)).encode("utf-8")
        try:
            preview_request = Request(base + "/api/preview", data=body, method="POST", headers=headers)
            preview = json.loads(urlopen(preview_request, timeout=3).read())
            self.assertEqual(
                {language: len(preview["selected"][language]) for language in ("zh", "en", "ru")},
                {"zh": 3, "en": 3, "ru": 1},
            )

            confirm_request = Request(base + "/api/confirm", data=body, method="POST", headers=headers)
            confirmed = json.loads(urlopen(confirm_request, timeout=3).read())
            self.assertEqual(confirmed["progress"]["cursors"], {"zh": 3, "en": 3, "ru": 1})
            self.assertEqual(fixture.gold_path.read_bytes(), original_gold)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_reviewer_frontend_preserves_review_contract_and_independent_hint_target(self) -> None:
        static_root = Path(__file__).parents[1] / "src" / "lotm_v2" / "review" / "static"
        javascript = (static_root / "app.js").read_text(encoding="utf-8")
        html = (static_root / "index.html").read_text(encoding="utf-8")

        self.assertIn("{count:Number(box.querySelector('.count').value)}", javascript)
        self.assertIn("$('#previewDialog').close();pending=null", javascript)
        self.assertIn("u.selected[l].map", javascript)
        self.assertNotIn("u.sides[l].selected", javascript)
        self.assertIn("function visibleForward(language,selected){return Math.max(displayForward[language]||4,selected)}", javascript)
        self.assertIn("goldReviewDisplayContext:${state.work_id}:${state.chapter}:${language}", javascript)
        self.assertIn('class="displayContext" data-language="${l}"', javascript)
        self.assertIn("setDisplayContext(x.dataset.language,x.value)", javascript)
        display_setter = javascript.split("function setDisplayContext", 1)[1].split("async function load", 1)[0]
        self.assertNotIn("api(", display_setter)
        self.assertIn("let hintBundle=null,hintParagraph=null,hintTargetId=null", javascript)
        self.assertIn("generation!==hintRequestGeneration", javascript)
        self.assertIn("bundle.paragraph_id!==target.id", javascript)
        self.assertIn("data-hint-target", javascript)
        generic_hint_loader = javascript.split("async function openHintTarget", 1)[1].split("function openHintPanelTarget", 1)[0]
        self.assertNotIn("scrollIntoView", generic_hint_loader)
        explicit_hint_opener = javascript.split("function openHintPanelTarget", 1)[1].split("function openHints", 1)[0]
        self.assertIn("$('#hintsPanel').hidden", explicit_hint_opener)
        self.assertIn("scrollIntoView", explicit_hint_opener)
        for control_id in ("hintPrevious", "hintNext", "hintGoTo", "hintGo", "hintCursor", "hintRetry"):
            self.assertIn(f'id="{control_id}"', html)

    def test_hints_api_and_glossary_preview_do_not_mutate_gold(self) -> None:
        class Translation:
            provider_id = "synthetic-translation:v1"
            def translate(self, text, source_language, target_language):
                return "Synthetic English hint"

        class Segmentation:
            provider_id = "synthetic-segmentation:v1"
            def segment(self, text):
                return (HintToken(0, text, 0, len(text)),)

        class Alignment:
            provider_id = "synthetic-alignment:v1"
            def align(self, zh_text, en_text, zh_tokens, en_tokens):
                return (LexicalAlignment(0, 1, 0, 1, 0.8),)

        fixture = Fixture(self.root)
        session = ReviewWorkspace(fixture.gold_path, self.root)
        hints = HintService(self.root / "cache", Translation(), Segmentation(), Alignment())
        glossary = GlossaryService(self.root / "glossary.json", "synthetic-work")
        server = ReviewHTTPServer(("127.0.0.1", 0), session, hints, glossary)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        original_gold = fixture.gold_path.read_bytes()
        try:
            paragraph = fixture.chapters[0].paragraphs[0]
            future_paragraph = fixture.chapters[0].paragraphs[2]
            en_paragraph = fixture.chapters[1].paragraphs[0]
            ru_paragraph = fixture.chapters[2].paragraphs[0]
            session_path = session.session_path
            self.assertIsNotNone(session_path)
            session.confirm(selection())
            self.assertEqual(session.snapshot()["progress"]["cursors"], {"zh": 1, "en": 1, "ru": 1})
            session_before = session_path.read_bytes()
            headers = {"Content-Type": "application/json", "X-Review-Token": server.review_token}

            hint_request = Request(
                base + "/api/hints", data=json.dumps({"paragraph_id": str(paragraph.id)}).encode(),
                method="POST", headers=headers,
            )
            hint = json.loads(urlopen(hint_request, timeout=3).read())
            self.assertEqual(hint["paragraph_id"], str(paragraph.id))
            self.assertEqual(hint["label"], "LOCAL MACHINE TRANSLATION HINT")
            self.assertEqual(hint["alignments"][0]["score"], 0.8)

            future_hint_request = Request(
                base + "/api/hints", data=json.dumps({"paragraph_id": str(future_paragraph.id)}).encode(),
                method="POST", headers=headers,
            )
            future_hint = json.loads(urlopen(future_hint_request, timeout=3).read())
            self.assertEqual(future_hint["paragraph_id"], str(future_paragraph.id))

            invalid_request = Request(
                base + "/api/hints", data=json.dumps({"paragraph_id": "en:p1"}).encode(),
                method="POST", headers=headers,
            )
            with self.assertRaises(HTTPError) as invalid_id_error:
                urlopen(invalid_request, timeout=3).read()
            self.assertEqual(invalid_id_error.exception.code, 400)

            en_request = Request(
                base + "/api/hints", data=json.dumps({"paragraph_id": str(en_paragraph.id)}).encode(),
                method="POST", headers=headers,
            )
            with self.assertRaises(HTTPError) as en_id_error:
                urlopen(en_request, timeout=3).read()
            self.assertEqual(en_id_error.exception.code, 400)

            ru_request = Request(
                base + "/api/hints", data=json.dumps({"paragraph_id": str(ru_paragraph.id)}).encode(),
                method="POST", headers=headers,
            )
            with self.assertRaises(HTTPError) as ru_id_error:
                urlopen(ru_request, timeout=3).read()
            self.assertEqual(ru_id_error.exception.code, 400)

            unknown_request = Request(
                base + "/api/hints", data=json.dumps({"paragraph_id": "synthetic-work:zh-test:0007:p999999"}).encode(),
                method="POST", headers=headers,
            )
            with self.assertRaises(HTTPError) as unknown_id_error:
                urlopen(unknown_request, timeout=3).read()
            self.assertEqual(unknown_id_error.exception.code, 400)

            preview_request = Request(
                base + "/api/glossary/preview",
                data=json.dumps({
                    "paragraph_id": str(future_paragraph.id),
                    "start_offset": 1,
                    "end_offset": 1 + len(future_paragraph.normalized_text[1:4]),
                    "zh_term": future_paragraph.normalized_text[1:4],
                    "en_term": "Synthetic term",
                }).encode(), method="POST", headers=headers,
            )
            preview = json.loads(urlopen(preview_request, timeout=3).read())
            self.assertFalse(preview["duplicate"])
            self.assertFalse(glossary.path.exists())
            self.assertEqual(preview["candidate"]["source"]["paragraph_id"], str(future_paragraph.id))
            self.assertEqual(preview["candidate"]["source"]["start_offset"], 1)
            self.assertEqual(preview["candidate"]["source"]["end_offset"], 1 + len(future_paragraph.normalized_text[1:4]))
            self.assertEqual(fixture.gold_path.read_bytes(), original_gold)
            self.assertEqual(session_path.read_bytes(), session_before)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
