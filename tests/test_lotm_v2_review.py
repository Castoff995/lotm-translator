from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from src.lotm_v2 import GOLD_SCHEMA_VERSION
from src.lotm_v2.domain import Chapter, ChapterId, Language, Paragraph, ParagraphId, ParagraphType, SourceId
from src.lotm_v2.gold.io import load_gold, save_gold
from src.lotm_v2.gold.model import (
    GoldAlignmentSide, GoldAlignmentUnit, GoldChapter, GoldSourceRef, GoldStatus, alignment_unit_id,
)
from src.lotm_v2.infrastructure.corpus_io import save_chapter
from src.lotm_v2.infrastructure.json_io import write_json_atomic
from src.lotm_v2.review import ReviewError, ReviewSession
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
        session = ReviewSession(fixture.gold_path, self.root)
        snapshot = session.snapshot()
        self.assertEqual(snapshot["progress"]["totals"], {"zh": 2, "en": 3, "ru": 4})
        result = session.confirm(selection())
        self.assertEqual(result["progress"]["cursors"], {"zh": 1, "en": 1, "ru": 1})

    def test_many_to_many_and_resume_reconstructs_cursors(self) -> None:
        fixture = Fixture(self.root)
        session = ReviewSession(fixture.gold_path, self.root)
        preview = session.preview(selection(2, 2, 2))
        self.assertEqual(len(preview["selected"]["en"]), 2)
        session.confirm(selection(2, 2, 2))
        reopened = ReviewSession(fixture.gold_path, self.root)
        self.assertEqual(reopened.snapshot()["progress"]["cursors"], {"zh": 2, "en": 2, "ru": 2})

    def test_explicit_gap_advances_only_non_gap_sources(self) -> None:
        fixture = Fixture(self.root, (1, 2, 1))
        session = ReviewSession(fixture.gold_path, self.root)
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
        session = ReviewSession(fixture.gold_path, self.root)
        result = session.disposition("ru", "metadata", "Synthetic legal line")
        self.assertEqual(result["progress"]["cursors"], {"zh": 0, "en": 0, "ru": 1})
        self.assertEqual(result["progress"]["dispositions"], 1)
        self.assertEqual(result["dispositions"][0]["paragraph"]["index"], 1)

    def test_undo_disposition_restores_source_cursor(self) -> None:
        fixture = Fixture(self.root)
        session = ReviewSession(fixture.gold_path, self.root)
        session.disposition("en", "separator")
        result = session.undo()
        self.assertEqual(result["progress"]["cursors"], {"zh": 0, "en": 0, "ru": 0})
        self.assertEqual(result["dispositions"], [])

    def test_rollback_removes_only_downstream_dispositions(self) -> None:
        fixture = Fixture(self.root)
        session = ReviewSession(fixture.gold_path, self.root)
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
        session = ReviewSession(fixture.gold_path, self.root)
        session.disposition("zh", "metadata")
        session.confirm(selection())
        session.disposition("ru", "footnote")
        reopened = ReviewSession(fixture.gold_path, self.root)
        self.assertEqual(reopened.snapshot()["progress"]["cursors"], {"zh": 2, "en": 1, "ru": 2})
        self.assertEqual(reopened.snapshot()["progress"]["dispositions"], 2)

    def test_hash_mismatch_blocks_opening(self) -> None:
        fixture = Fixture(self.root)
        fixture.source_paths[0].write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ReviewError, "integrity mismatch"):
            ReviewSession(fixture.gold_path, self.root)

    def test_unknown_schema_is_rejected(self) -> None:
        fixture = Fixture(self.root)
        gold = load_gold(fixture.gold_path)
        save_gold(fixture.gold_path, replace(gold, schema_version="99.0"))
        with self.assertRaisesRegex(ReviewError, "Unsupported Gold schema"):
            ReviewSession(fixture.gold_path, self.root)

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
            ReviewSession(fixture.gold_path, self.root)

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
            ReviewSession(fixture.gold_path, self.root)

    def test_undo_and_rollback_remove_downstream_boundaries(self) -> None:
        fixture = Fixture(self.root)
        session = ReviewSession(fixture.gold_path, self.root)
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
        session = ReviewSession(fixture.gold_path, self.root)
        session.confirm(selection())
        session.confirm(selection())
        after = session.gold.alignment_units[0].id
        session.set_boundary(after, "JOIN", "same semantic unit")
        result = session.set_boundary(after, "BREAK")
        self.assertEqual(result["boundaries"], [{"after": after, "decision": "BREAK", "note": None}])
        self.assertEqual(ReviewSession(fixture.gold_path, self.root).snapshot()["boundaries"][0]["decision"], "BREAK")

    def test_finish_requires_all_physical_paragraphs_and_boundaries(self) -> None:
        fixture = Fixture(self.root, (2, 2, 2), heading=True)
        session = ReviewSession(fixture.gold_path, self.root)
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
        session = ReviewSession(fixture.gold_path, self.root)
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
        session = ReviewSession(fixture.gold_path, self.root)
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
            self.assertEqual(len(load_gold(fixture.gold_path).paragraph_dispositions), 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
