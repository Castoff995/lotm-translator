from __future__ import annotations

from dataclasses import replace
import hashlib
import http.client
import json
import multiprocessing
import queue
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from src.lotm_v2.domain import (
    Chapter, ChapterId, Language, Paragraph, ParagraphId, ParagraphType, SourceId,
)
from src.lotm_v2.gold.io import save_gold
from src.lotm_v2.gold.model import (
    BoundaryDecision, GoldAlignmentSide, GoldAlignmentUnit, GoldBoundary, GoldChapter,
    GoldSourceRef, GoldStatus, alignment_unit_id,
)
from src.lotm_v2.gold.pairwise.draft import confirm_pairwise_gold, create_pairwise_gold_draft
from src.lotm_v2.gold.pairwise.io import (
    load_pairwise_gold, pairwise_gold_document_sha256, pairwise_gold_json_bytes,
    replace_pairwise_gold_atomic, save_pairwise_gold,
)
from src.lotm_v2.gold.pairwise.model import (
    PairwiseAlignmentSide, PairwiseAlignmentUnit, PairwiseDirection,
    PairwiseGoldStatus, PairwiseParagraphDisposition, pairwise_alignment_unit_id,
)
from src.lotm_v2.gold.model import ParagraphDispositionReason
from src.lotm_v2.infrastructure.corpus_io import save_chapter
from src.lotm_v2.infrastructure.canonical_json import canonical_json_sha256
from src.lotm_v2.infrastructure.file_lock import ExclusiveFileLock
from src.lotm_v2.infrastructure.paths import PathPolicy
from src.lotm_v2.pairwise_review.compatibility import (
    PAIRWISE_REVIEW_APP_VERSION, PAIRWISE_REVIEW_SEMANTICS_VERSION,
    PAIRWISE_REVIEW_SESSION_SCHEMA_VERSION,
)
from src.lotm_v2.pairwise_review.server import PairwiseReviewHTTPServer
from src.lotm_v2.pairwise_review.service import PairwiseReviewError, PairwiseReviewWorkspace
from src.lotm_v2.pairwise_review.session_io import (
    LEGACY_PAIRWISE_SESSION_MESSAGE, active_pairwise_session_metadata,
    load_pairwise_session, pairwise_session_from_dict, pairwise_session_to_dict,
    save_pairwise_session,
)
from src.lotm_v2.pairwise_review.session_model import (
    PairwiseRequestRecord, PairwiseReviewSessionStatus,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def chapter(language: Language, source_value: str, count: int) -> Chapter:
    source, chapter_id = SourceId(source_value), ChapterId("lotm", 1)
    return Chapter(
        "2.2-draft", chapter_id, source, language,
        tuple(
            Paragraph(
                ParagraphId("lotm", source, 1, index), source, language, chapter_id, index,
                f"raw {source_value} {index}", f"text {source_value} {index}", ParagraphType.STORY,
            )
            for index in range(1, count + 1)
        ),
    )


class Fixture:
    def __init__(self, root: Path, count: int = 3) -> None:
        self.root, self.paths = root, PathPolicy(root)
        self.left, self.right = chapter(Language.ZH, "zh", count), chapter(Language.EN, "en", count)
        self.left_path = self.paths.normalized_chapter(self.left.source, 1)
        self.right_path = self.paths.normalized_chapter(self.right.source, 1)
        save_chapter(self.left_path, self.left); save_chapter(self.right_path, self.right)
        self.draft = create_pairwise_gold_draft(
            self.left, self.right, self.left_path, self.right_path,
            PairwiseDirection.ZH_EN, self.paths,
        )
        self.gold_path = self.paths.pairwise_gold_chapter(self.left.source, self.right.source, 1)
        save_pairwise_gold(self.gold_path, self.draft)

    def unit(self, index: int, left: tuple[int, ...], right: tuple[int, ...]) -> PairwiseAlignmentUnit:
        return PairwiseAlignmentUnit(
            pairwise_alignment_unit_id(self.left.id, self.left.source, self.right.source, index),
            PairwiseAlignmentSide(paragraphs=tuple(self.left.paragraphs[item - 1].id for item in left)),
            PairwiseAlignmentSide(paragraphs=tuple(self.right.paragraphs[item - 1].id for item in right)),
        )

    def replace_target(self, gold) -> None:
        replace_pairwise_gold_atomic(self.gold_path, gold)


def selection(left: int = 1, right: int = 1) -> dict[str, object]:
    return {"sides": {"left": {"count": left}, "right": {"count": right}}}


def commit(workspace: PairwiseReviewWorkspace, preview: dict[str, object], request_id: str | None = None):
    return workspace.confirm_preview(
        str(preview["preview_token"]), request_id or str(uuid.uuid4()),
        int(preview["session_revision"]),
    )


def _process_preview_commit(
    root: str, gold_path: str, ready: object, go: object, results: object,
) -> None:
    try:
        workspace = PairwiseReviewWorkspace(Path(gold_path), Path(root))
        preview = workspace.preview(selection(), workspace.session_document.revision)
        ready.put(True)
        go.wait(10)
        commit(workspace, preview)
        results.put(("ok", workspace.session_document.revision))
    except BaseException as error:
        results.put(("error", str(error)))


def _process_publish(
    root: str, gold_path: str, ready: object, go: object, results: object,
) -> None:
    try:
        workspace = PairwiseReviewWorkspace(Path(gold_path), Path(root))
        ready.put(True)
        go.wait(10)
        result = workspace.publish(
            explicit_confirmation=True,
            expected_revision=workspace.session_document.revision,
            request_id=str(uuid.uuid4()),
        )
        results.put(("ok", result["publication"]["published"]))
    except BaseException as error:
        results.put(("error", str(error)))


def _process_confirm_gold(
    root: str, gold_path: str, document_hash: str, file_hash: str,
    ready: object, go: object, results: object,
) -> None:
    try:
        ready.put(True)
        go.wait(10)
        value = confirm_pairwise_gold(
            Path(gold_path), Path(root), document_hash, file_hash,
        )
        results.put(("ok", value.status.value))
    except BaseException as error:
        results.put(("error", str(error)))


class PairwiseReviewLifecycleTests(unittest.TestCase):
    def test_session_contract_round_trip_and_versions_do_not_copy_source_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory)); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            document = workspace.session_document
            self.assertEqual(document.session_schema_version, PAIRWISE_REVIEW_SESSION_SCHEMA_VERSION)
            self.assertEqual(document.review_semantics_version, PAIRWISE_REVIEW_SEMANTICS_VERSION)
            self.assertEqual(document.created_with_reviewer, PAIRWISE_REVIEW_APP_VERSION)
            payload = pairwise_session_to_dict(document)
            self.assertEqual(pairwise_session_from_dict(payload), document)
            serialized = json.dumps(payload)
            self.assertNotIn("text zh", serialized); self.assertNotIn("text en", serialized)
            payload["unexpected"] = True
            with self.assertRaisesRegex(ValueError, "unknown"):
                pairwise_session_from_dict(payload)

    def test_preview_confirm_gap_disposition_undo_rollback_and_resume_are_session_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory)); before = f.gold_path.read_bytes()
            workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            commit(workspace, workspace.preview(selection(), workspace.session_document.revision))
            commit(workspace, workspace.preview_disposition(
                "right", "metadata", workspace.session_document.revision,
            ))
            commit(workspace, workspace.preview(selection(), workspace.session_document.revision))
            gap_selection = {"sides": {"left": {"count": 1}, "right": {"gap": True, "reason": "omission"}}}
            commit(workspace, workspace.preview(gap_selection, workspace.session_document.revision))
            self.assertEqual(workspace.cursors, {"left": 3, "right": 3})
            self.assertEqual(f.gold_path.read_bytes(), before)
            session_id = workspace.session_document.session_id
            resumed = PairwiseReviewWorkspace(f.gold_path, f.root)
            self.assertEqual(resumed.session_document.session_id, session_id)
            self.assertEqual(resumed.cursors, {"left": 3, "right": 3})
            resumed.undo(resumed.session_document.revision, str(uuid.uuid4()))
            self.assertEqual(resumed.cursors, {"left": 2, "right": 3})
            commit(resumed, resumed.preview_rollback(1, resumed.session_document.revision))
            self.assertEqual(resumed.cursors, {"left": 1, "right": 1})
            self.assertEqual(len(resumed.gold.paragraph_dispositions), 0)
            self.assertEqual(f.gold_path.read_bytes(), before)

    def test_stale_preview_split_merge_and_edit_disposition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=3)
            baseline = replace(
                f.draft,
                alignment_units=(f.unit(1, (1, 2), (1, 2)), f.unit(2, (3,), (3,))),
            )
            f.replace_target(baseline)
            workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            split = workspace.preview_split(
                baseline.alignment_units[0].id, {"left": 1, "right": 1},
                workspace.session_document.revision,
            )
            commit(workspace, split)
            self.assertEqual(len(workspace.gold.alignment_units), 3)
            with self.assertRaisesRegex(PairwiseReviewError, "revision conflict"):
                workspace.confirm_preview(
                    str(split["preview_token"]), str(uuid.uuid4()),
                    int(split["session_revision"]),
                )
            merge = workspace.preview_merge(
                workspace.gold.alignment_units[0].id, workspace.session_document.revision,
            )
            commit(workspace, merge)
            self.assertEqual(len(workspace.gold.alignment_units), 2)

        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=2)
            baseline = replace(
                f.draft, alignment_units=(f.unit(1, (1,), (1,)),),
                paragraph_dispositions=(
                    PairwiseParagraphDisposition(
                        f.left.paragraphs[1].id, ParagraphDispositionReason.METADATA,
                        after_alignment_unit=1,
                    ),
                    PairwiseParagraphDisposition(
                        f.right.paragraphs[1].id, ParagraphDispositionReason.METADATA,
                        after_alignment_unit=1,
                    ),
                ),
            )
            f.replace_target(baseline); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            paragraph_id = str(f.left.paragraphs[1].id)
            preview = workspace.preview_disposition_edit(
                paragraph_id, "other", "human reason", workspace.session_document.revision,
            )
            commit(workspace, preview)
            edited = next(item for item in workspace.gold.paragraph_dispositions if str(item.paragraph_id) == paragraph_id)
            self.assertEqual((edited.reason.value, edited.note), ("other", "human reason"))

    def test_full_validation_publication_draft_only_confirmation_read_only_and_discard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=2); before = f.gold_path.read_bytes()
            workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            for _ in range(2):
                commit(workspace, workspace.preview(
                    selection(), workspace.session_document.revision,
                ))
            self.assertTrue(workspace.finish()["valid"])
            self.assertEqual(f.gold_path.read_bytes(), before)
            with self.assertRaisesRegex(PairwiseReviewError, "explicit confirmation"):
                workspace.publish(
                    explicit_confirmation=False,
                    expected_revision=workspace.session_document.revision,
                    request_id=str(uuid.uuid4()),
                )
            result = workspace.publish(
                explicit_confirmation=True,
                expected_revision=workspace.session_document.revision,
                request_id=str(uuid.uuid4()),
            )
            self.assertTrue(result["publication"]["published"])
            self.assertEqual(load_pairwise_gold(f.gold_path).status, PairwiseGoldStatus.DRAFT)
            active = f.paths.active_pairwise_review_session("lotm", f.left.source, f.right.source, 1)
            self.assertFalse(active.exists())
            draft = load_pairwise_gold(f.gold_path)
            confirm_pairwise_gold(
                f.gold_path, f.root, pairwise_gold_document_sha256(draft),
                sha256(f.gold_path),
            )
            reopened = PairwiseReviewWorkspace(f.gold_path, f.root)
            self.assertTrue(reopened.read_only); self.assertIsNone(reopened.session_document)
            with self.assertRaisesRegex(PairwiseReviewError, "active"):
                reopened.preview_disposition(
                    "left", "metadata", 0,
                )

        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory)); before = f.gold_path.read_bytes()
            workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            workspace.discard(workspace.session_document.revision, str(uuid.uuid4()))
            self.assertEqual(f.gold_path.read_bytes(), before)

    def test_optimistic_concurrency_preserves_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=1); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            commit(workspace, workspace.preview(selection(), workspace.session_document.revision))
            external = replace(load_pairwise_gold(f.gold_path), notes="external edit")
            replace_pairwise_gold_atomic(f.gold_path, external)
            active = workspace.session_path
            with self.assertRaisesRegex(PairwiseReviewError, "baseline changed"):
                workspace.publish(
                    explicit_confirmation=True,
                    expected_revision=workspace.session_document.revision,
                    request_id=str(uuid.uuid4()),
                )
            self.assertTrue(active.exists())

    def test_resume_recovers_crash_after_atomic_gold_write_and_source_mismatch_preserves_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=1); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            commit(workspace, workspace.preview(selection(), workspace.session_document.revision))
            expected = pairwise_gold_document_sha256(workspace.gold)
            intent = replace(
                workspace.session_document,
                publication_request_id=str(uuid.uuid4()),
                publication_expected_revision=workspace.session_document.revision,
                publication_expected_document_sha256=expected,
                publication_started_at="2026-01-01T00:00:00+00:00",
            )
            save_pairwise_session(workspace.session_path, intent)
            replace_pairwise_gold_atomic(f.gold_path, workspace.gold)
            recovered = PairwiseReviewWorkspace(f.gold_path, f.root)
            self.assertTrue(recovered.publication_recovered)
            self.assertEqual(recovered.session_document.status.value, "published")
            self.assertFalse(f.paths.active_pairwise_review_session("lotm", f.left.source, f.right.source, 1).exists())

        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory)); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            active = workspace.session_path
            changed = replace(f.left, metadata={"changed": True}); save_chapter(f.left_path, changed)
            with self.assertRaisesRegex(Exception, "SHA-256 mismatch"):
                PairwiseReviewWorkspace(f.gold_path, f.root)
            self.assertTrue(active.exists())

    def test_bootstrap_convenience_only_fills_counts_and_does_not_mutate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); f = Fixture(root, count=2)
            ru = chapter(Language.RU, "ru-official", 1); ru_path = f.paths.normalized_chapter(ru.source, 1); save_chapter(ru_path, ru)
            refs = (
                GoldSourceRef(f.left.source, Language.ZH, f.paths.relative(f.left_path), sha256(f.left_path)),
                GoldSourceRef(f.right.source, Language.EN, f.paths.relative(f.right_path), sha256(f.right_path)),
                GoldSourceRef(ru.source, Language.RU, f.paths.relative(ru_path), sha256(ru_path)),
            )
            unit = GoldAlignmentUnit(
                alignment_unit_id(f.left.id, 1),
                GoldAlignmentSide(paragraphs=tuple(item.id for item in f.left.paragraphs)),
                GoldAlignmentSide(paragraphs=tuple(item.id for item in f.right.paragraphs)),
                GoldAlignmentSide(paragraphs=(ru.paragraphs[0].id,)),
            )
            tri = GoldChapter("1.0-draft", GoldStatus.DRAFT, f.left.id, refs, (unit,), ())
            tri_path = f.paths.gold_chapter(1); save_gold(tri_path, tri)
            before = f.gold_path.read_bytes()
            workspace = PairwiseReviewWorkspace(f.gold_path, f.root, tri_path)
            self.assertEqual(workspace.gold.provenance.creation_mode.value, "trilingual_bootstrap")
            self.assertEqual(
                workspace.gold.provenance.bootstrap_trilingual_gold_document_sha256,
                workspace.bootstrap["trilingual_gold_document_sha256"],
            )
            working_before = pairwise_gold_document_sha256(workspace.gold)
            result = workspace.use_remaining_bootstrap_span()
            self.assertEqual(result["sides"], {"left": {"count": 2}, "right": {"count": 2}})
            self.assertFalse(result["mutated"]); self.assertFalse(result["confirmed"])
            self.assertEqual(pairwise_gold_document_sha256(workspace.gold), working_before)
            self.assertEqual(f.gold_path.read_bytes(), before)
            self.assertIn("NOT PAIRWISE TRUTH", workspace.bootstrap["label"])
            self.assertTrue(workspace.bootstrap["items"][0]["third_language_context"]["paragraph_text"])

    def test_http_preview_confirm_and_frontend_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=2); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            server = PairwiseReviewHTTPServer(("127.0.0.1", 0), workspace)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                preview_request = {**selection(), "expected_revision": 0}
                connection.request("POST", "/api/preview", json.dumps(preview_request), {
                    "Content-Type": "application/json", "X-Review-Token": server.review_token,
                })
                response = connection.getresponse(); preview = json.loads(response.read())
                self.assertEqual(response.status, 200)
                body = json.dumps({
                    "preview_token": preview["preview_token"],
                    "request_id": str(uuid.uuid4()),
                    "expected_revision": preview["session_revision"],
                })
                connection.request("POST", "/api/confirm", body, {
                    "Content-Type": "application/json", "X-Review-Token": server.review_token,
                })
                response = connection.getresponse(); state = json.loads(response.read())
                self.assertEqual(response.status, 200); self.assertEqual(len(state["alignment_units"]), 1)
                stale_request = {**selection(), "expected_revision": 0}
                connection.request("POST", "/api/preview", json.dumps(stale_request), {
                    "Content-Type": "application/json", "X-Review-Token": server.review_token,
                })
                response = connection.getresponse(); conflict = json.loads(response.read())
                self.assertEqual(response.status, 409)
                self.assertIn("Session changed in another tab or process", conflict["error"])
                self.assertEqual(workspace.session_document.revision, 1)
                connection.request("POST", "/api/preview", json.dumps(selection()), {
                    "Content-Type": "application/json", "X-Review-Token": server.review_token,
                })
                response = connection.getresponse(); missing = json.loads(response.read())
                self.assertEqual(response.status, 400)
                self.assertIn("expected_revision", missing["error"])
                self.assertEqual(workspace.session_document.revision, 1)
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)
        root = Path(__file__).resolve().parents[1]
        html = (root / "src/lotm_v2/pairwise_review/static/index.html").read_text(encoding="utf-8")
        js = (root / "src/lotm_v2/pairwise_review/static/app.js").read_text(encoding="utf-8")
        self.assertIn("Pairwise Gold Review Tool", html)
        self.assertIn("Use remaining bootstrap span", html)
        for action in ("/api/preview", "/api/confirm", "/api/disposition/preview", "/api/undo", "/api/rollback/preview", "/api/publish"):
            self.assertIn(action, js)
        self.assertNotIn("JOIN", html); self.assertNotIn("BREAK", html)

    def test_legacy_pairwise_session_is_metadata_only_and_fails_closed_without_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory)); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            active = workspace.session_path
            payload = pairwise_session_to_dict(workspace.session_document)
            payload["session_schema_version"] = "1.0-draft"
            payload["review_semantics_version"] = "1.0"
            active.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            before = active.read_bytes()
            metadata = active_pairwise_session_metadata(f.paths.pairwise_review_sessions())
            self.assertTrue(metadata[0]["legacy_requires_explicit_decision"])
            with self.assertRaisesRegex(PairwiseReviewError, LEGACY_PAIRWISE_SESSION_MESSAGE):
                PairwiseReviewWorkspace(f.gold_path, f.root)
            self.assertEqual(active.read_bytes(), before)

    def test_preview_tokens_bind_exact_actions_and_do_not_accept_client_substitution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=3); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            client = selection(1, 1)
            preview = workspace.preview(client, workspace.session_document.revision)
            client["sides"]["left"]["count"] = 3
            client["sides"]["right"]["count"] = 3
            commit(workspace, preview)
            self.assertEqual(workspace.cursors, {"left": 1, "right": 1})

            disposition = workspace.preview_disposition(
                "left", "metadata", workspace.session_document.revision, "A",
            )
            commit(workspace, disposition)
            self.assertEqual(workspace.gold.paragraph_dispositions[-1].reason.value, "metadata")
            self.assertEqual(workspace.gold.paragraph_dispositions[-1].note, "A")

        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=3)
            baseline = replace(
                f.draft,
                alignment_units=(f.unit(1, (1, 2), (1, 2)), f.unit(2, (3,), (3,))),
            )
            f.replace_target(baseline); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            split = workspace.preview_split(
                baseline.alignment_units[0].id, {"left": 1, "right": 1},
                workspace.session_document.revision,
            )
            commit(workspace, split)
            self.assertEqual(
                tuple(len(item.left.paragraphs) for item in workspace.gold.alignment_units),
                (1, 1, 1),
            )
            merge = workspace.preview_merge(
                workspace.gold.alignment_units[0].id, workspace.session_document.revision,
            )
            commit(workspace, merge)
            self.assertEqual(len(workspace.gold.alignment_units[0].left.paragraphs), 2)
            rollback = workspace.preview_rollback(1, workspace.session_document.revision)
            commit(workspace, rollback)
            self.assertEqual(len(workspace.gold.alignment_units), 1)

        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=2)
            baseline = replace(
                f.draft, alignment_units=(f.unit(1, (1,), (1,)),),
                paragraph_dispositions=(PairwiseParagraphDisposition(
                    f.left.paragraphs[1].id, ParagraphDispositionReason.METADATA,
                    after_alignment_unit=1,
                ),),
            )
            f.replace_target(baseline); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            paragraph_id = str(f.left.paragraphs[1].id)
            edit = workspace.preview_disposition_edit(
                paragraph_id, "other", "exact A", workspace.session_document.revision,
            )
            commit(workspace, edit)
            self.assertEqual(workspace.gold.paragraph_dispositions[0].note, "exact A")

    def test_preview_invalidation_unknown_expired_other_session_and_restart_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=2); first = PairwiseReviewWorkspace(f.gold_path, f.root)
            stale = first.preview(selection(), first.session_document.revision)
            commit(first, first.preview(selection(), first.session_document.revision))
            before = pairwise_gold_document_sha256(first.gold)
            with self.assertRaisesRegex(PairwiseReviewError, "revision conflict"):
                commit(first, stale)
            with self.assertRaisesRegex(PairwiseReviewError, "unknown or expired"):
                first.confirm_preview("unknown", str(uuid.uuid4()), first.session_document.revision)

            wrong_operation = first.preview_disposition(
                "left", "metadata", first.session_document.revision,
            )
            with self.assertRaisesRegex(PairwiseReviewError, "another operation"):
                first.confirm_preview(
                    str(wrong_operation["preview_token"]), str(uuid.uuid4()),
                    int(wrong_operation["session_revision"]), "confirm_unit",
                )

            fresh = first.preview(selection(), first.session_document.revision)
            record = first._previews[str(fresh["preview_token"])]
            first._previews[str(fresh["preview_token"])] = replace(record, expires_at=0.0)
            with self.assertRaisesRegex(PairwiseReviewError, "unknown or expired"):
                commit(first, fresh)
            self.assertEqual(pairwise_gold_document_sha256(first.gold), before)

            restarted = PairwiseReviewWorkspace(f.gold_path, f.root)
            with self.assertRaisesRegex(PairwiseReviewError, "unknown or expired"):
                restarted.confirm_preview(
                    str(stale["preview_token"]), str(uuid.uuid4()),
                    restarted.session_document.revision,
                )

        with tempfile.TemporaryDirectory() as left_directory, tempfile.TemporaryDirectory() as right_directory:
            left = Fixture(Path(left_directory), count=1)
            right = Fixture(Path(right_directory), count=1)
            first = PairwiseReviewWorkspace(left.gold_path, left.root)
            second = PairwiseReviewWorkspace(right.gold_path, right.root)
            token = first.preview(selection(), first.session_document.revision)
            with self.assertRaisesRegex(PairwiseReviewError, "unknown or expired"):
                second.confirm_preview(
                    str(token["preview_token"]), str(uuid.uuid4()),
                    second.session_document.revision,
                )

    def test_two_workspace_cas_and_real_multiprocessing_prevent_lost_updates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=2)
            first = PairwiseReviewWorkspace(f.gold_path, f.root)
            second = PairwiseReviewWorkspace(f.gold_path, f.root)
            first_preview = first.preview(selection(), first.session_document.revision)
            second_preview = second.preview(selection(), second.session_document.revision)
            commit(first, first_preview)
            with self.assertRaisesRegex(PairwiseReviewError, "revision conflict"):
                commit(second, second_preview)
            persisted = load_pairwise_session(first.session_path)
            self.assertEqual(persisted.revision, 1)
            self.assertEqual(len(persisted.working_gold.alignment_units), 1)

        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=2)
            context = multiprocessing.get_context("spawn")
            ready, go, results = context.Queue(), context.Event(), context.Queue()
            processes = [
                context.Process(
                    target=_process_preview_commit,
                    args=(str(f.root), str(f.gold_path), ready, go, results),
                )
                for _ in range(2)
            ]
            for process in processes:
                process.start()
            ready.get(timeout=15); ready.get(timeout=15); go.set()
            outcomes = [results.get(timeout=20), results.get(timeout=20)]
            for process in processes:
                process.join(timeout=20)
                self.assertFalse(process.is_alive())
            self.assertEqual([item[0] for item in outcomes].count("ok"), 1)
            self.assertEqual([item[0] for item in outcomes].count("error"), 1)
            self.assertIn("revision conflict", next(item[1] for item in outcomes if item[0] == "error"))
            active = f.paths.active_pairwise_review_session("lotm", f.left.source, f.right.source, 1)
            persisted = load_pairwise_session(active)
            self.assertEqual((persisted.revision, len(persisted.journal)), (1, 1))

    def test_all_semantic_previews_reject_stale_but_structurally_valid_clients(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=2)
            base = replace(
                f.draft, alignment_units=(f.unit(1, (1,), (1,)),),
                paragraph_dispositions=(PairwiseParagraphDisposition(
                    f.left.paragraphs[1].id, ParagraphDispositionReason.METADATA,
                    after_alignment_unit=1,
                ),),
            )
            f.replace_target(base)
            first = PairwiseReviewWorkspace(f.gold_path, f.root)
            second = PairwiseReviewWorkspace(f.gold_path, f.root)
            stale_revision = second.session_document.revision
            commit(first, first.preview_disposition_edit(
                str(f.left.paragraphs[1].id), "translator_note", None,
                first.session_document.revision,
            ))
            committed_hash = pairwise_gold_document_sha256(first.gold)
            with self.assertRaisesRegex(
                PairwiseReviewError, "Session changed in another tab or process",
            ):
                second.preview_disposition_edit(
                    str(f.left.paragraphs[1].id), "footnote", None, stale_revision,
                )
            persisted = load_pairwise_session(first.session_path)
            self.assertEqual(persisted.revision, 1)
            self.assertEqual(
                persisted.working_gold.paragraph_dispositions[0].reason,
                ParagraphDispositionReason.TRANSLATOR_NOTE,
            )
            self.assertEqual(pairwise_gold_document_sha256(persisted.working_gold), committed_hash)
            self.assertFalse(second._previews)

        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=3)
            first = PairwiseReviewWorkspace(f.gold_path, f.root)
            second = PairwiseReviewWorkspace(f.gold_path, f.root)
            stale_revision = second.session_document.revision
            commit(first, first.preview_disposition(
                "left", "metadata", first.session_document.revision,
            ))
            with self.assertRaisesRegex(PairwiseReviewError, "revision conflict"):
                second.preview(selection(), stale_revision)
            fresh = second.preview(selection(), second.session_document.revision)
            self.assertEqual(fresh["session_revision"], 1)

        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=3)
            base = replace(
                f.draft,
                alignment_units=(f.unit(1, (1,), (1,)), f.unit(2, (2,), (2,))),
                paragraph_dispositions=(
                    PairwiseParagraphDisposition(
                        f.left.paragraphs[2].id, ParagraphDispositionReason.METADATA,
                        after_alignment_unit=2,
                    ),
                    PairwiseParagraphDisposition(
                        f.right.paragraphs[2].id, ParagraphDispositionReason.METADATA,
                        after_alignment_unit=2,
                    ),
                ),
            )
            f.replace_target(base)
            first = PairwiseReviewWorkspace(f.gold_path, f.root)
            second = PairwiseReviewWorkspace(f.gold_path, f.root)
            stale_revision = second.session_document.revision
            commit(first, first.preview_disposition_edit(
                str(f.left.paragraphs[2].id), "translator_note", None,
                first.session_document.revision,
            ))
            with self.assertRaisesRegex(PairwiseReviewError, "revision conflict"):
                second.preview_merge(base.alignment_units[0].id, stale_revision)
            with self.assertRaisesRegex(PairwiseReviewError, "revision conflict"):
                second.preview_rollback(1, stale_revision)
            fresh = second.preview_merge(
                base.alignment_units[0].id, second.session_document.revision,
            )
            self.assertEqual(fresh["operation"], "merge")

        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=3)
            base = replace(
                f.draft,
                alignment_units=(f.unit(1, (1, 2), (1, 2)),),
                paragraph_dispositions=(
                    PairwiseParagraphDisposition(
                        f.left.paragraphs[2].id, ParagraphDispositionReason.METADATA,
                        after_alignment_unit=1,
                    ),
                    PairwiseParagraphDisposition(
                        f.right.paragraphs[2].id, ParagraphDispositionReason.METADATA,
                        after_alignment_unit=1,
                    ),
                ),
            )
            f.replace_target(base)
            first = PairwiseReviewWorkspace(f.gold_path, f.root)
            second = PairwiseReviewWorkspace(f.gold_path, f.root)
            stale_revision = second.session_document.revision
            commit(first, first.preview_disposition_edit(
                str(f.left.paragraphs[2].id), "translator_note", None,
                first.session_document.revision,
            ))
            with self.assertRaisesRegex(PairwiseReviewError, "revision conflict"):
                second.preview_split(
                    base.alignment_units[0].id, {"left": 1, "right": 1},
                    stale_revision,
                )

    def test_correction_aware_undo_restores_each_semantic_action_after_resume(self) -> None:
        def undo_after_restart(workspace: PairwiseReviewWorkspace, expected_hash: str) -> None:
            resumed = PairwiseReviewWorkspace(workspace.gold_path, workspace.root)
            resumed.undo(resumed.session_document.revision, str(uuid.uuid4()))
            self.assertEqual(pairwise_gold_document_sha256(resumed.gold), expected_hash)

        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=1); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            baseline = pairwise_gold_document_sha256(workspace.gold)
            commit(workspace, workspace.preview(selection(), workspace.session_document.revision))
            undo_after_restart(workspace, baseline)

        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=1); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            baseline = pairwise_gold_document_sha256(workspace.gold)
            commit(workspace, workspace.preview_disposition(
                "left", "metadata", workspace.session_document.revision,
            ))
            undo_after_restart(workspace, baseline)

        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=3)
            base = replace(
                f.draft,
                alignment_units=(f.unit(1, (1, 2), (1, 2)), f.unit(2, (3,), (3,))),
            )
            f.replace_target(base); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            baseline = pairwise_gold_document_sha256(workspace.gold)
            commit(workspace, workspace.preview_split(
                base.alignment_units[0].id, {"left": 1, "right": 1},
                workspace.session_document.revision,
            ))
            undo_after_restart(workspace, baseline)

        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=3)
            base = replace(
                f.draft,
                alignment_units=(f.unit(1, (1,), (1,)), f.unit(2, (2,), (2,)), f.unit(3, (3,), (3,))),
            )
            f.replace_target(base); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            baseline = pairwise_gold_document_sha256(workspace.gold)
            commit(workspace, workspace.preview_merge(
                base.alignment_units[0].id, workspace.session_document.revision,
            ))
            undo_after_restart(workspace, baseline)

        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=2)
            base = replace(
                f.draft, alignment_units=(f.unit(1, (1,), (1,)),),
                paragraph_dispositions=(PairwiseParagraphDisposition(
                    f.left.paragraphs[1].id, ParagraphDispositionReason.METADATA,
                    after_alignment_unit=1,
                ),),
            )
            f.replace_target(base); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            baseline = pairwise_gold_document_sha256(workspace.gold)
            commit(workspace, workspace.preview_disposition_edit(
                str(f.left.paragraphs[1].id), "other", "human",
                workspace.session_document.revision,
            ))
            undo_after_restart(workspace, baseline)

        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=3)
            base = replace(
                f.draft,
                alignment_units=(f.unit(1, (1,), (1,)), f.unit(2, (2,), (2,)), f.unit(3, (3,), (3,))),
            )
            f.replace_target(base); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            baseline = pairwise_gold_document_sha256(workspace.gold)
            commit(workspace, workspace.preview_rollback(
                1, workspace.session_document.revision,
            ))
            undo_after_restart(workspace, baseline)

    def test_request_idempotency_survives_restart_and_rejects_semantic_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=3); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            preview = workspace.preview(
                selection(), workspace.session_document.revision,
            ); action_id = str(uuid.uuid4())
            first = commit(workspace, preview, action_id)
            second = commit(workspace, preview, action_id)
            self.assertFalse(first["idempotent"]); self.assertTrue(second["idempotent"])
            self.assertEqual(workspace.session_document.revision, 1)
            self.assertEqual(len(workspace.gold.alignment_units), 1)

            disposition = workspace.preview_disposition(
                "left", "metadata", workspace.session_document.revision,
            )
            with self.assertRaisesRegex(PairwiseReviewError, "different semantic action"):
                commit(workspace, disposition, action_id)

            disposition_id = str(uuid.uuid4()); commit(workspace, disposition, disposition_id)
            duplicate_disposition = commit(workspace, disposition, disposition_id)
            self.assertTrue(duplicate_disposition["idempotent"])
            self.assertEqual(len(workspace.gold.paragraph_dispositions), 1)
            resumed = PairwiseReviewWorkspace(f.gold_path, f.root)
            self.assertEqual(resumed.session_document.revision, 2)

            undo_id = str(uuid.uuid4())
            before_undo = resumed.session_document.revision
            resumed.undo(before_undo, undo_id)
            restarted = PairwiseReviewWorkspace(f.gold_path, f.root)
            duplicate = restarted.undo(before_undo, undo_id)
            self.assertTrue(duplicate["idempotent"])
            self.assertEqual(restarted.session_document.revision, before_undo + 1)

        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=3)
            base = replace(
                f.draft,
                alignment_units=(f.unit(1, (1, 2), (1, 2)), f.unit(2, (3,), (3,))),
            )
            f.replace_target(base); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            split = workspace.preview_split(
                base.alignment_units[0].id, {"left": 1, "right": 1},
                workspace.session_document.revision,
            )
            action_id = str(uuid.uuid4())
            commit(workspace, split, action_id); duplicate = commit(workspace, split, action_id)
            self.assertTrue(duplicate["idempotent"])
            self.assertEqual((workspace.session_document.revision, len(workspace.gold.alignment_units)), (1, 3))

    def test_journal_replay_detects_payload_working_gold_and_baseline_tampering(self) -> None:
        for tamper in ("journal", "working", "baseline"):
            with self.subTest(tamper=tamper), tempfile.TemporaryDirectory() as directory:
                f = Fixture(Path(directory), count=2); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
                commit(workspace, workspace.preview(
                    selection(), workspace.session_document.revision,
                ))
                active = workspace.session_path
                payload = pairwise_session_to_dict(load_pairwise_session(active))
                if tamper == "journal":
                    payload["journal"][0]["payload"]["unit"]["note"] = "tampered"
                elif tamper == "working":
                    payload["working_gold"]["notes"] = "tampered"
                else:
                    payload["baseline_gold"]["notes"] = "tampered"
                active.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                with self.assertRaisesRegex(
                    (PairwiseReviewError, ValueError),
                    "diverged|differs|replay|hash",
                ):
                    PairwiseReviewWorkspace(f.gold_path, f.root)

    def test_journal_invariants_are_persistent_and_contain_no_source_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=2); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            commit(workspace, workspace.preview(selection(), workspace.session_document.revision))
            commit(workspace, workspace.preview_disposition(
                "left", "metadata", workspace.session_document.revision,
            ))
            document = load_pairwise_session(workspace.session_path)
            self.assertEqual(tuple(item.sequence for item in document.journal), (1, 2))
            self.assertEqual(len({item.request_id for item in document.journal}), 2)
            self.assertEqual(
                document.journal[0].after_working_gold_sha256,
                document.journal[1].before_working_gold_sha256,
            )
            encoded = json.dumps([item.payload for item in document.journal])
            self.assertNotIn("text zh", encoded); self.assertNotIn("text en", encoded)
            self.assertEqual(load_pairwise_gold(f.gold_path), f.draft)

    def test_shared_gold_lock_serializes_publication_and_confirmation_processes(self) -> None:
        context = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=1); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            commit(workspace, workspace.preview(selection(), workspace.session_document.revision))
            ready, go, results = context.Queue(), context.Event(), context.Queue()
            process = context.Process(
                target=_process_publish,
                args=(str(f.root), str(f.gold_path), ready, go, results),
            )
            process.start(); ready.get(timeout=15)
            lock_path = f.paths.pairwise_resource_lock("gold", f.gold_path)
            with ExclusiveFileLock(lock_path):
                go.set()
                with self.assertRaises(queue.Empty):
                    results.get(timeout=0.3)
            outcome = results.get(timeout=20)
            process.join(timeout=20); self.assertFalse(process.is_alive())
            self.assertEqual(outcome, ("ok", True))

        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=1)
            complete = replace(f.draft, alignment_units=(f.unit(1, (1,), (1,)),))
            f.replace_target(complete)
            ready, go, results = context.Queue(), context.Event(), context.Queue()
            process = context.Process(
                target=_process_confirm_gold,
                args=(
                    str(f.root), str(f.gold_path), pairwise_gold_document_sha256(complete),
                    sha256(f.gold_path), ready, go, results,
                ),
            )
            process.start(); ready.get(timeout=15)
            with ExclusiveFileLock(f.paths.pairwise_resource_lock("gold", f.gold_path)):
                go.set()
                with self.assertRaises(queue.Empty):
                    results.get(timeout=0.3)
            outcome = results.get(timeout=20)
            process.join(timeout=20); self.assertFalse(process.is_alive())
            self.assertEqual(outcome, ("ok", "confirmed"))

    def test_archive_and_publication_crash_boundaries_recover_without_manual_json(self) -> None:
        def leave_archive_and_active(active: Path, archive: Path, document: object) -> Path:
            save_pairwise_session(archive, document)
            return archive

        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory)); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            active = workspace.session_path
            with patch(
                "src.lotm_v2.pairwise_review.service.archive_pairwise_session",
                side_effect=leave_archive_and_active,
            ):
                workspace.discard(workspace.session_document.revision, str(uuid.uuid4()))
            archive = workspace.session_path
            self.assertTrue(active.exists()); self.assertTrue(archive.exists())
            recovered = PairwiseReviewWorkspace(f.gold_path, f.root)
            self.assertFalse(active.exists())
            self.assertEqual(recovered.session_document.status.value, "discarded")

        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory)); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            active = workspace.session_path
            with patch(
                "src.lotm_v2.pairwise_review.service.archive_pairwise_session",
                side_effect=RuntimeError("before archive creation"),
            ), self.assertRaisesRegex(RuntimeError, "before archive creation"):
                workspace.discard(workspace.session_document.revision, str(uuid.uuid4()))
            self.assertTrue(active.exists())
            self.assertEqual(PairwiseReviewWorkspace(f.gold_path, f.root).session_document.status.value, "active")

        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=1); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            commit(workspace, workspace.preview(selection(), workspace.session_document.revision))
            before = f.gold_path.read_bytes(); active = workspace.session_path
            with patch(
                "src.lotm_v2.pairwise_review.service.replace_pairwise_gold_atomic",
                side_effect=RuntimeError("before target replace"),
            ), self.assertRaisesRegex(RuntimeError, "before target replace"):
                workspace.publish(
                    explicit_confirmation=True,
                    expected_revision=workspace.session_document.revision,
                    request_id=str(uuid.uuid4()),
                )
            self.assertEqual(f.gold_path.read_bytes(), before); self.assertTrue(active.exists())
            resumed = PairwiseReviewWorkspace(f.gold_path, f.root)
            self.assertEqual(resumed.session_document.status.value, "active")
            self.assertIsNone(resumed.session_document.publication_request_id)

        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=1); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            commit(workspace, workspace.preview(
                selection(), workspace.session_document.revision,
            )); active = workspace.session_path
            with patch(
                "src.lotm_v2.pairwise_review.service.archive_pairwise_session",
                side_effect=RuntimeError("after target before archive"),
            ), self.assertRaisesRegex(RuntimeError, "after target before archive"):
                workspace.publish(
                    explicit_confirmation=True,
                    expected_revision=workspace.session_document.revision,
                    request_id=str(uuid.uuid4()),
                )
            self.assertTrue(active.exists())
            self.assertEqual(load_pairwise_gold(f.gold_path).alignment_units, workspace.gold.alignment_units)
            recovered = PairwiseReviewWorkspace(f.gold_path, f.root)
            self.assertTrue(recovered.publication_recovered)
            self.assertEqual(recovered.session_document.status.value, "published")
            self.assertFalse(active.exists())

    def test_atomic_session_write_failure_preserves_original_session_and_gold(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=1); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            preview = workspace.preview(selection(), workspace.session_document.revision)
            session_before = workspace.session_path.read_bytes()
            gold_before = f.gold_path.read_bytes()
            with patch(
                "src.lotm_v2.infrastructure.json_io.os.replace",
                side_effect=OSError("synthetic replace failure"),
            ), self.assertRaisesRegex(OSError, "synthetic replace failure"):
                commit(workspace, preview)
            self.assertEqual(workspace.session_path.read_bytes(), session_before)
            self.assertEqual(f.gold_path.read_bytes(), gold_before)
            resumed = PairwiseReviewWorkspace(f.gold_path, f.root)
            self.assertEqual((resumed.session_document.revision, len(resumed.gold.alignment_units)), (0, 0))

    def test_terminal_publish_and_discard_requests_are_idempotent_in_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=1); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            commit(workspace, workspace.preview(selection(), workspace.session_document.revision))
            expected = workspace.session_document.revision; request = str(uuid.uuid4())
            workspace.publish(
                explicit_confirmation=True, expected_revision=expected, request_id=request,
            )
            duplicate = workspace.publish(
                explicit_confirmation=True, expected_revision=expected, request_id=request,
            )
            self.assertTrue(duplicate["idempotent"])

        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory)); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            expected = workspace.session_document.revision; request = str(uuid.uuid4())
            workspace.discard(expected, request)
            duplicate = workspace.discard(expected, request)
            self.assertTrue(duplicate["idempotent"])

    def test_terminal_discard_receipt_is_durable_across_restart_and_semantic_reuse_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory)); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            request = str(uuid.uuid4())
            first = workspace.discard(workspace.session_document.revision, request)
            archive = workspace.session_path
            active = f.paths.active_pairwise_review_session(
                "lotm", f.left.source, f.right.source, 1,
            )
            self.assertFalse(first["idempotent"])
            self.assertFalse(active.exists())
            self.assertEqual(len(tuple(archive.parent.glob("*.discarded.json"))), 1)

            restarted = PairwiseReviewWorkspace(f.gold_path, f.root)
            self.assertIsNone(restarted.session_document)
            retry = restarted.discard(0, request)
            self.assertTrue(retry["idempotent"])
            self.assertEqual(restarted.session_path, archive)
            self.assertFalse(active.exists())
            self.assertEqual(len(tuple(archive.parent.glob("*.discarded.json"))), 1)

            mismatch = PairwiseReviewWorkspace(f.gold_path, f.root)
            with self.assertRaisesRegex(PairwiseReviewError, "different semantic action"):
                mismatch.publish(
                    explicit_confirmation=True, expected_revision=0, request_id=request,
                )
            self.assertFalse(active.exists())

            legitimate = PairwiseReviewWorkspace(f.gold_path, f.root)
            new_request = str(uuid.uuid4())
            result = legitimate.discard(0, new_request)
            self.assertFalse(result["idempotent"])
            self.assertFalse(active.exists())
            self.assertEqual(len(tuple(archive.parent.glob("*.discarded.json"))), 2)

    def test_terminal_publish_receipt_is_durable_across_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=1)
            complete = replace(f.draft, alignment_units=(f.unit(1, (1,), (1,)),))
            f.replace_target(complete)
            workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            request = str(uuid.uuid4())
            first = workspace.publish(
                explicit_confirmation=True,
                expected_revision=workspace.session_document.revision,
                request_id=request,
            )
            archive = workspace.session_path
            active = f.paths.active_pairwise_review_session(
                "lotm", f.left.source, f.right.source, 1,
            )
            self.assertFalse(first["idempotent"])
            self.assertFalse(active.exists())
            self.assertEqual(len(tuple(archive.parent.glob("*.published.json"))), 1)

            restarted = PairwiseReviewWorkspace(f.gold_path, f.root)
            self.assertIsNone(restarted.session_document)
            retry = restarted.publish(
                explicit_confirmation=True, expected_revision=0, request_id=request,
            )
            self.assertTrue(retry["idempotent"])
            self.assertEqual(retry["publication"]["file_sha256"], first["publication"]["file_sha256"])
            self.assertEqual(restarted.session_path, archive)
            self.assertFalse(active.exists())
            self.assertEqual(len(tuple(archive.parent.glob("*.published.json"))), 1)

    def test_malformed_terminal_receipt_fails_closed_before_session_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory)); workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            workspace.discard(workspace.session_document.revision, str(uuid.uuid4()))
            archive = workspace.session_path
            active = f.paths.active_pairwise_review_session(
                "lotm", f.left.source, f.right.source, 1,
            )
            archive.write_text("{malformed", encoding="utf-8")
            with self.assertRaisesRegex(PairwiseReviewError, "Invalid Pairwise terminal receipt"):
                PairwiseReviewWorkspace(f.gold_path, f.root)
            self.assertFalse(active.exists())

    def test_active_recovery_rejects_status_only_discard_archive_byte_identically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), count=1)
            workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
            commit(workspace, workspace.preview(
                selection(), workspace.session_document.revision,
            ))
            active = workspace.session_path
            active_before = active.read_bytes()
            fake = replace(
                workspace.session_document,
                status=PairwiseReviewSessionStatus.DISCARDED,
            )
            archive = f.paths.archived_pairwise_review_session(
                "lotm", f.left.source, f.right.source, 1,
                fake.session_id, fake.status.value,
            )
            save_pairwise_session(archive, fake)
            archive_before = archive.read_bytes()

            with self.assertRaisesRegex(
                PairwiseReviewError, "exactly one terminal request",
            ):
                PairwiseReviewWorkspace(f.gold_path, f.root)

            self.assertTrue(active.exists())
            self.assertEqual(active.read_bytes(), active_before)
            self.assertEqual(archive.read_bytes(), archive_before)

    def test_active_recovery_rejects_invalid_terminal_semantics_for_both_statuses(self) -> None:
        cases = (
            (PairwiseReviewSessionStatus.DISCARDED, "zero", ()),
            (PairwiseReviewSessionStatus.DISCARDED, "wrong_action", (
                ("publish", {"explicit_confirmation": True}),
            )),
            (PairwiseReviewSessionStatus.DISCARDED, "wrong_payload", (
                ("discard", {"unexpected": True}),
            )),
            (PairwiseReviewSessionStatus.DISCARDED, "duplicate", (
                ("discard", {}), ("discard", {}),
            )),
            (PairwiseReviewSessionStatus.PUBLISHED, "zero", ()),
            (PairwiseReviewSessionStatus.PUBLISHED, "wrong_action", (
                ("discard", {}),
            )),
            (PairwiseReviewSessionStatus.PUBLISHED, "wrong_payload", (
                ("publish", {"explicit_confirmation": False}),
            )),
            (PairwiseReviewSessionStatus.PUBLISHED, "duplicate", (
                ("publish", {"explicit_confirmation": True}),
                ("publish", {"explicit_confirmation": True}),
            )),
        )
        for status, label, operations in cases:
            with self.subTest(status=status.value, case=label):
                with tempfile.TemporaryDirectory() as directory:
                    f = Fixture(Path(directory), count=1)
                    workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
                    active = workspace.session_path
                    active_before = active.read_bytes()
                    document = workspace.session_document
                    records = []
                    revision = document.revision
                    working_hash = pairwise_gold_document_sha256(document.working_gold)
                    for operation, payload in operations:
                        revision += 1
                        records.append(PairwiseRequestRecord(
                            str(uuid.uuid4()), operation,
                            canonical_json_sha256({
                                "operation": operation, "payload": payload,
                            }),
                            revision, working_hash, "2026-01-01T00:00:00+00:00",
                        ))
                    terminal = replace(
                        document, status=status, revision=revision,
                        request_history=document.request_history + tuple(records),
                        published_gold_file_sha256=(
                            hashlib.sha256(
                                pairwise_gold_json_bytes(document.working_gold)
                            ).hexdigest()
                            if status is PairwiseReviewSessionStatus.PUBLISHED else None
                        ),
                        published_at=(
                            "2026-01-01T00:00:00+00:00"
                            if status is PairwiseReviewSessionStatus.PUBLISHED else None
                        ),
                    )
                    archive = f.paths.archived_pairwise_review_session(
                        "lotm", f.left.source, f.right.source, 1,
                        terminal.session_id, terminal.status.value,
                    )
                    save_pairwise_session(archive, terminal)
                    archive_before = archive.read_bytes()

                    expected = (
                        "exactly one terminal request"
                        if label in {"zero", "duplicate"}
                        else "result or payload semantics diverged"
                    )
                    with self.assertRaisesRegex(PairwiseReviewError, expected):
                        PairwiseReviewWorkspace(f.gold_path, f.root)

                    self.assertTrue(active.exists())
                    self.assertEqual(active.read_bytes(), active_before)
                    self.assertEqual(archive.read_bytes(), archive_before)

    def test_active_recovery_accepts_valid_discarded_and_published_archives(self) -> None:
        for status in (
            PairwiseReviewSessionStatus.DISCARDED,
            PairwiseReviewSessionStatus.PUBLISHED,
        ):
            with self.subTest(status=status.value):
                with tempfile.TemporaryDirectory() as directory:
                    f = Fixture(Path(directory), count=1)
                    workspace = PairwiseReviewWorkspace(f.gold_path, f.root)
                    commit(workspace, workspace.preview(
                        selection(), workspace.session_document.revision,
                    ))
                    active = workspace.session_path
                    request_id = str(uuid.uuid4())
                    with patch.object(Path, "unlink", return_value=None):
                        if status is PairwiseReviewSessionStatus.DISCARDED:
                            workspace.discard(
                                workspace.session_document.revision, request_id,
                            )
                        else:
                            workspace.publish(
                                explicit_confirmation=True,
                                expected_revision=workspace.session_document.revision,
                                request_id=request_id,
                            )
                    archive = workspace.session_path
                    self.assertTrue(active.exists())
                    self.assertTrue(archive.exists())

                    recovered = PairwiseReviewWorkspace(f.gold_path, f.root)

                    self.assertFalse(active.exists())
                    self.assertEqual(recovered.session_path, archive)
                    self.assertEqual(recovered.session_document.status, status)
                    self.assertEqual(
                        recovered.publication_recovered,
                        status is PairwiseReviewSessionStatus.PUBLISHED,
                    )

    def test_ui_contract_uses_candidate_token_modal_local_error_and_pending_guards(self) -> None:
        root = Path(__file__).resolve().parents[1]
        html = (root / "src/lotm_v2/pairwise_review/static/index.html").read_text(encoding="utf-8")
        css = (root / "src/lotm_v2/pairwise_review/static/styles.css").read_text(encoding="utf-8")
        js = (root / "src/lotm_v2/pairwise_review/static/app.js").read_text(encoding="utf-8")
        self.assertIn('<dialog id="previewDialog">', html)
        self.assertIn('id="dialogError"', html)
        self.assertIn("position:sticky", css); self.assertIn("overflow:auto", css)
        self.assertIn("preview_token: currentPreview.token", js)
        self.assertIn("expected_revision: state.session?.revision ?? 0", js)
        self.assertIn("expected_revision: currentPreview.revision", js)
        self.assertIn("request_id: currentPreview.requestId", js)
        self.assertNotIn("expected_working_gold_sha256", js)
        self.assertNotIn("confirm(JSON.stringify", js)
        self.assertIn("if (pending || !currentPreview) return", js)
        self.assertIn('$("#dialogError").textContent = error.message', js)


if __name__ == "__main__":
    unittest.main()
