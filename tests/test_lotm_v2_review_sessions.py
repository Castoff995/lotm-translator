from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
import hashlib
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from src.lotm_v2.cli import main as cli_main
from src.lotm_v2.gold.io import gold_document_sha256, load_gold, save_gold
from src.lotm_v2.gold.model import GoldStatus
from src.lotm_v2.gold.validation import validate_gold_chapter
from src.lotm_v2.infrastructure.json_io import write_json_atomic
from src.lotm_v2.infrastructure.paths import PathPolicy
from src.lotm_v2.review import ReviewError, ReviewWorkspace
from src.lotm_v2.review.compatibility import REVIEW_APP_VERSION
from src.lotm_v2.review.server import ReviewHTTPServer
from src.lotm_v2.review.session_io import load_session, save_session

from test_lotm_v2_review import Fixture, selection


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PersistentReviewSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def active_path(self) -> Path:
        return PathPolicy(self.root).active_review_session("synthetic-work", 7)

    def test_session_document_round_trip_contains_decisions_but_not_source_text(self) -> None:
        fixture = Fixture(self.root)
        workspace = ReviewWorkspace(fixture.gold_path, self.root)
        workspace.confirm(selection())
        document = load_session(self.active_path())
        self.assertEqual(document.session_id, workspace.session_document.session_id)
        self.assertEqual(len(document.working_gold.alignment_units), 1)
        serialized = self.active_path().read_text(encoding="utf-8")
        self.assertNotIn("text zh-test 1", serialized)
        self.assertNotIn("raw zh-test 1", serialized)
        self.assertNotIn("translation_hint", serialized)
        self.assertNotIn("glossary", serialized)

    def test_ordinary_actions_autosave_only_session_and_leave_gold_byte_identical(self) -> None:
        fixture = Fixture(self.root, (4, 4, 4))
        original = fixture.gold_path.read_bytes()
        original_sources = [path.read_bytes() for path in fixture.source_paths]
        with patch("src.lotm_v2.review.service.save_gold") as forbidden_gold_write:
            workspace = ReviewWorkspace(fixture.gold_path, self.root)
            workspace.confirm(selection())
            workspace.confirm(selection())
            first = workspace.gold.alignment_units[0].id
            workspace.set_boundary(first, "JOIN", "synthetic")
            workspace.disposition("ru", "metadata", "synthetic")
            workspace.undo()
            workspace.confirm(selection())
            workspace.rollback(2)
            forbidden_gold_write.assert_not_called()
        self.assertEqual(fixture.gold_path.read_bytes(), original)
        self.assertEqual([path.read_bytes() for path in fixture.source_paths], original_sources)
        self.assertTrue(self.active_path().is_file())
        self.assertEqual(list(self.active_path().parent.glob(f".{self.active_path().name}.*.tmp")), [])
        reopened = ReviewWorkspace(fixture.gold_path, self.root)
        self.assertEqual(reopened.snapshot()["progress"]["cursors"], {"zh": 2, "en": 2, "ru": 2})
        self.assertEqual(reopened.snapshot()["progress"]["units"], 2)
        self.assertEqual(reopened.snapshot()["progress"]["boundaries_reviewed"], 1)

    def test_existing_draft_gold_decisions_become_the_new_session_baseline(self) -> None:
        fixture = Fixture(self.root, (2, 2, 2))
        first = ReviewWorkspace(fixture.gold_path, self.root)
        first.confirm(selection())
        existing_draft = first.gold
        first.discard()
        save_gold(fixture.gold_path, existing_draft)
        baseline_bytes = fixture.gold_path.read_bytes()

        reopened = ReviewWorkspace(fixture.gold_path, self.root)
        self.assertEqual(len(reopened.gold.alignment_units), 1)
        self.assertEqual(reopened.snapshot()["progress"]["cursors"], {"zh": 1, "en": 1, "ru": 1})
        self.assertEqual(fixture.gold_path.read_bytes(), baseline_bytes)

    def test_restart_restores_same_id_cursors_units_dispositions_and_boundaries(self) -> None:
        fixture = Fixture(self.root, (3, 3, 3))
        workspace = ReviewWorkspace(fixture.gold_path, self.root)
        session_id = workspace.session_document.session_id
        workspace.confirm(selection())
        workspace.confirm(selection())
        workspace.set_boundary(workspace.gold.alignment_units[0].id, "BREAK")
        workspace.disposition("zh", "heading", "synthetic")
        expected = workspace.snapshot()

        reopened = ReviewWorkspace(fixture.gold_path, self.root)
        actual = reopened.snapshot()
        self.assertEqual(actual["session"]["session_id"], session_id)
        self.assertEqual(actual["progress"]["cursors"], expected["progress"]["cursors"])
        self.assertEqual(actual["units"], expected["units"])
        self.assertEqual(actual["dispositions"], expected["dispositions"])
        self.assertEqual(actual["boundaries"], expected["boundaries"])

    def test_app_version_only_changes_are_compatible_in_both_directions(self) -> None:
        fixture = Fixture(self.root)
        workspace = ReviewWorkspace(fixture.gold_path, self.root)
        session_id = workspace.session_document.session_id
        payload = json.loads(self.active_path().read_text(encoding="utf-8"))
        for compatible_version in ("0.1.0", "99.0.0"):
            payload["created_with_reviewer"] = compatible_version
            payload["last_opened_with_reviewer"] = compatible_version
            write_json_atomic(self.active_path(), payload)
            reopened = ReviewWorkspace(fixture.gold_path, self.root)
            self.assertEqual(reopened.session_document.session_id, session_id)
            self.assertEqual(reopened.session_document.created_with_reviewer, compatible_version)
            self.assertEqual(reopened.session_document.last_opened_with_reviewer, REVIEW_APP_VERSION)
            self.assertEqual(reopened.session_document.session_schema_version, payload["session_schema_version"])
            self.assertEqual(reopened.session_document.review_semantics_version, payload["review_semantics_version"])
            payload = json.loads(self.active_path().read_text(encoding="utf-8"))

    def test_unknown_session_schema_fails_closed_without_mutating_file(self) -> None:
        fixture = Fixture(self.root)
        ReviewWorkspace(fixture.gold_path, self.root)
        payload = json.loads(self.active_path().read_text(encoding="utf-8"))
        payload["session_schema_version"] = "99.0"
        write_json_atomic(self.active_path(), payload)
        before = self.active_path().read_bytes()
        with self.assertRaisesRegex(ReviewError, "no migration"):
            ReviewWorkspace(fixture.gold_path, self.root)
        self.assertEqual(self.active_path().read_bytes(), before)

    def test_unknown_review_semantics_fails_closed_without_mutating_file(self) -> None:
        fixture = Fixture(self.root)
        ReviewWorkspace(fixture.gold_path, self.root)
        payload = json.loads(self.active_path().read_text(encoding="utf-8"))
        payload["review_semantics_version"] = "99.0"
        write_json_atomic(self.active_path(), payload)
        before = self.active_path().read_bytes()
        with self.assertRaisesRegex(ReviewError, "Unsupported review semantics"):
            ReviewWorkspace(fixture.gold_path, self.root)
        self.assertEqual(self.active_path().read_bytes(), before)

    def test_normalized_source_change_blocks_resume_and_preserves_session(self) -> None:
        fixture = Fixture(self.root)
        ReviewWorkspace(fixture.gold_path, self.root).confirm(selection())
        session_before = self.active_path().read_bytes()
        fixture.source_paths[0].write_bytes(fixture.source_paths[0].read_bytes() + b"\n")
        with self.assertRaisesRegex(ReviewError, "source mismatch"):
            ReviewWorkspace(fixture.gold_path, self.root)
        self.assertEqual(self.active_path().read_bytes(), session_before)

    def test_session_source_identity_change_blocks_resume(self) -> None:
        fixture = Fixture(self.root)
        ReviewWorkspace(fixture.gold_path, self.root)
        payload = json.loads(self.active_path().read_text(encoding="utf-8"))
        payload["working_gold"]["sources"][0]["source_id"] = "zh-other-revision"
        write_json_atomic(self.active_path(), payload)
        before = self.active_path().read_bytes()
        with self.assertRaisesRegex(ReviewError, "source mismatch"):
            ReviewWorkspace(fixture.gold_path, self.root)
        self.assertEqual(self.active_path().read_bytes(), before)

    def test_external_gold_change_blocks_publication_and_preserves_session(self) -> None:
        fixture = Fixture(self.root, (1, 1, 1))
        workspace = ReviewWorkspace(fixture.gold_path, self.root)
        workspace.confirm(selection())
        save_gold(fixture.gold_path, replace(load_gold(fixture.gold_path), notes="external edit"))
        session_before = self.active_path().read_bytes()
        with self.assertRaisesRegex(ReviewError, "baseline conflict"):
            workspace.publish()
        self.assertTrue(self.active_path().is_file())
        self.assertEqual(self.active_path().read_bytes(), session_before)

    def test_missing_paragraph_id_in_working_gold_blocks_resume(self) -> None:
        fixture = Fixture(self.root)
        ReviewWorkspace(fixture.gold_path, self.root).confirm(selection())
        payload = json.loads(self.active_path().read_text(encoding="utf-8"))
        payload["working_gold"]["alignment_units"][0]["zh"]["paragraphs"][0] = (
            "synthetic-work:zh-test:0007:p999999"
        )
        write_json_atomic(self.active_path(), payload)
        before = self.active_path().read_bytes()
        with self.assertRaisesRegex(ReviewError, "Invalid unit"):
            ReviewWorkspace(fixture.gold_path, self.root)
        self.assertEqual(self.active_path().read_bytes(), before)

    def test_complete_session_publishes_atomically_and_archives(self) -> None:
        fixture = Fixture(self.root, (2, 2, 2))
        original_hash = sha256(fixture.gold_path)
        workspace = ReviewWorkspace(fixture.gold_path, self.root)
        workspace.confirm(selection())
        workspace.confirm(selection())
        workspace.set_boundary(workspace.gold.alignment_units[0].id, "JOIN")
        self.assertTrue(workspace.finish()["valid"])
        result = workspace.publish()
        self.assertNotEqual(sha256(fixture.gold_path), original_hash)
        self.assertEqual(result["publication"]["gold_sha256"], sha256(fixture.gold_path))
        self.assertEqual(result["session"]["status"], "published")
        self.assertFalse(self.active_path().exists())
        self.assertTrue(Path(result["session"]["path"]).is_file())
        published = load_gold(fixture.gold_path)
        validate_gold_chapter(published, fixture.chapters)

    def test_incomplete_session_cannot_publish_and_gold_is_unchanged(self) -> None:
        fixture = Fixture(self.root, (2, 2, 2))
        original = fixture.gold_path.read_bytes()
        workspace = ReviewWorkspace(fixture.gold_path, self.root)
        workspace.confirm(selection())
        with self.assertRaisesRegex(ReviewError, "Missing Gold fate"):
            workspace.publish()
        self.assertEqual(fixture.gold_path.read_bytes(), original)
        self.assertTrue(self.active_path().is_file())

    def test_discard_archives_session_only(self) -> None:
        fixture = Fixture(self.root)
        original = fixture.gold_path.read_bytes()
        workspace = ReviewWorkspace(fixture.gold_path, self.root)
        workspace.confirm(selection())
        result = workspace.discard()
        self.assertEqual(result["session"]["status"], "discarded")
        self.assertEqual(fixture.gold_path.read_bytes(), original)
        self.assertFalse(self.active_path().exists())
        self.assertTrue(Path(result["session"]["path"]).is_file())

    def test_confirmed_gold_is_read_only_and_creates_no_active_session(self) -> None:
        fixture = Fixture(self.root)
        save_gold(fixture.gold_path, replace(load_gold(fixture.gold_path), status=GoldStatus.CONFIRMED))
        workspace = ReviewWorkspace(fixture.gold_path, self.root)
        self.assertTrue(workspace.read_only)
        self.assertIsNone(workspace.snapshot()["session"])
        self.assertFalse(self.active_path().exists())

    def test_recoverable_crash_after_gold_write_finalizes_without_rewrite(self) -> None:
        fixture = Fixture(self.root, (1, 1, 1))
        workspace = ReviewWorkspace(fixture.gold_path, self.root)
        workspace.confirm(selection())
        expected = gold_document_sha256(workspace.gold)
        intent = replace(
            workspace.session_document,
            publication_expected_gold_sha256=expected,
            publication_started_at="2026-08-21T00:00:00Z",
        )
        save_session(self.active_path(), intent)
        save_gold(fixture.gold_path, workspace.gold)
        published_bytes = fixture.gold_path.read_bytes()

        recovered = ReviewWorkspace(fixture.gold_path, self.root)
        self.assertEqual(fixture.gold_path.read_bytes(), published_bytes)
        self.assertTrue(recovered.snapshot()["session"]["publication_recovered"])
        self.assertEqual(recovered.snapshot()["session"]["status"], "published")
        self.assertFalse(self.active_path().exists())

    def test_cli_discovers_and_reports_active_session(self) -> None:
        fixture = Fixture(self.root)
        workspace = ReviewWorkspace(fixture.gold_path, self.root)
        session_id = workspace.session_document.session_id
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(cli_main(["review-session-list", "--root", str(self.root)]), 0)
        listing = json.loads(output.getvalue())
        self.assertEqual(listing[0]["session_id"], session_id)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(cli_main([
                "review-session-status", str(fixture.gold_path), "--root", str(self.root),
            ]), 0)
        status = json.loads(output.getvalue())
        self.assertTrue(status["active"])
        self.assertEqual(status["session"]["session_id"], session_id)
        self.assertNotIn("working_gold", status["session"])

    def test_publish_http_endpoint_requires_explicit_confirmation(self) -> None:
        fixture = Fixture(self.root, (1, 1, 1))
        workspace = ReviewWorkspace(fixture.gold_path, self.root)
        workspace.confirm(selection())
        server = ReviewHTTPServer(("127.0.0.1", 0), workspace)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        headers = {"Content-Type": "application/json", "X-Review-Token": server.review_token}
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_port}/api/publish",
                data=b"{}", method="POST", headers=headers,
            )
            with self.assertRaises(HTTPError) as caught:
                urlopen(request, timeout=3)
            self.assertEqual(caught.exception.code, 400)
            self.assertTrue(self.active_path().is_file())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_review_session_json_schema_is_valid_json_and_declares_gold_ref(self) -> None:
        schema = json.loads(
            (Path(__file__).parents[1] / "schemas" / "v2" / "review-session.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(schema["properties"]["session_schema_version"]["const"], "1.0-draft")
        self.assertEqual(schema["properties"]["review_semantics_version"]["const"], "1.0")
        self.assertEqual(schema["properties"]["working_gold"]["$ref"], "gold-chapter.schema.json")


if __name__ == "__main__":
    unittest.main()
