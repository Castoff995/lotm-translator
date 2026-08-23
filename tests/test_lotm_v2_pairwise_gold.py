from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
import hashlib
import io
import json
import ast
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.lotm_v2.alignment.evaluation import evaluate_proposal_files, evaluate_validated_proposal
from src.lotm_v2.alignment.io import proposal_document_sha256, save_proposal
from src.lotm_v2.alignment.model import (
    PAIRWISE_ALIGNMENT_PROPOSAL_ARTIFACT_TYPE, PAIRWISE_ALIGNMENT_PROPOSAL_SCHEMA_VERSION,
    AlignmentDirection, CoverageMode, PairwiseAlignmentProposal, ProducerProvenance,
    ProposalSide, ProposalSourceSnapshot, ProposalUnit, parameters_sha256, proposal_unit_id,
)
from src.lotm_v2.alignment.validation import load_and_validate_proposal
from src.lotm_v2.cli import main as cli_main
from src.lotm_v2.domain import (
    Chapter, ChapterId, Language, Paragraph, ParagraphId, ParagraphType, SourceId,
)
from src.lotm_v2.gold.io import gold_document_sha256, save_gold
from src.lotm_v2.gold.model import (
    BoundaryDecision, GoldAlignmentSide, GoldAlignmentUnit, GoldBoundary, GoldChapter,
    GoldFlag, GoldGap, GoldSourceRef, GoldStatus, ParagraphDisposition,
    ParagraphDispositionReason, alignment_unit_id,
)
from src.lotm_v2.gold.pairwise.bootstrap import inspect_trilingual_bootstrap
from src.lotm_v2.gold.pairwise.draft import confirm_pairwise_gold, create_pairwise_gold_draft
from src.lotm_v2.gold.pairwise.io import (
    load_pairwise_gold, pairwise_gold_document_sha256, pairwise_gold_file_sha256,
    pairwise_gold_from_dict, pairwise_gold_to_dict, replace_pairwise_gold_atomic,
    save_pairwise_gold,
)
from src.lotm_v2.gold.pairwise.model import (
    PAIRWISE_GOLD_ARTIFACT_TYPE, PAIRWISE_GOLD_SCHEMA_VERSION,
    PairwiseAlignmentSide, PairwiseAlignmentUnit, PairwiseCreationMode,
    PairwiseDirection, PairwiseGap, PairwiseGold, PairwiseGoldProvenance,
    PairwiseGoldSource, PairwiseGoldStatus, PairwiseParagraphDisposition,
    pairwise_alignment_unit_id,
)
from src.lotm_v2.gold.pairwise.validation import (
    PairwiseGoldValidationError, load_and_validate_pairwise_gold, validate_pairwise_gold,
)
from src.lotm_v2.gold.validation import validate_gold_chapter
from src.lotm_v2.infrastructure.corpus_io import save_chapter
from src.lotm_v2.infrastructure.paths import PathPolicy


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_chapter(language: Language, source_id: str, count: int, number: int = 1) -> Chapter:
    source, chapter = SourceId(source_id), ChapterId("lotm", number)
    return Chapter(
        "2.2-draft", chapter, source, language,
        tuple(
            Paragraph(
                ParagraphId("lotm", source, number, index), source, language, chapter, index,
                f"raw {source_id} {index}", f"text {source_id} {index}", ParagraphType.STORY,
            )
            for index in range(1, count + 1)
        ),
    )


def pside(chapter: Chapter, *indices: int) -> PairwiseAlignmentSide:
    return PairwiseAlignmentSide(paragraphs=tuple(chapter.paragraphs[index - 1].id for index in indices))


def gside(chapter: Chapter, *indices: int) -> GoldAlignmentSide:
    return GoldAlignmentSide(paragraphs=tuple(chapter.paragraphs[index - 1].id for index in indices))


class PairwiseFixture:
    def __init__(
        self, root: Path, direction: PairwiseDirection = PairwiseDirection.ZH_EN,
        left_count: int = 4, right_count: int = 4,
    ) -> None:
        self.root, self.paths, self.direction = root, PathPolicy(root), direction
        self.left = make_chapter(Language.ZH, "zh", left_count)
        right_language = direction.right_language
        self.right = make_chapter(right_language, "en" if right_language is Language.EN else "ru-official", right_count)
        self.left_path = self.paths.normalized_chapter(self.left.source, 1)
        self.right_path = self.paths.normalized_chapter(self.right.source, 1)
        save_chapter(self.left_path, self.left); save_chapter(self.right_path, self.right)
        self.draft = create_pairwise_gold_draft(
            self.left, self.right, self.left_path, self.right_path, direction, self.paths,
        )

    def unit(self, index: int, left: tuple[int, ...] | None, right: tuple[int, ...] | None) -> PairwiseAlignmentUnit:
        side = lambda chapter, values: (
            PairwiseAlignmentSide(gap=PairwiseGap(GoldFlag.EDITION_DIFFERENCE))
            if values is None else pside(chapter, *values)
        )
        return PairwiseAlignmentUnit(
            pairwise_alignment_unit_id(
                self.left.id, self.left.source, self.right.source, index,
            ), side(self.left, left), side(self.right, right),
        )

    def complete(self, status: PairwiseGoldStatus = PairwiseGoldStatus.DRAFT) -> PairwiseGold:
        if len(self.left.paragraphs) != 4 or len(self.right.paragraphs) != 4:
            units = tuple(
                self.unit(index, (index,), (index,))
                for index in range(1, min(len(self.left.paragraphs), len(self.right.paragraphs)) + 1)
            )
            return replace(self.draft, status=status, alignment_units=units)
        return replace(
            self.draft, status=status,
            alignment_units=(
                self.unit(1, (1,), (1,)), self.unit(2, (2, 3), (2,)), self.unit(3, (4,), (3,)),
            ),
            paragraph_dispositions=(
                PairwiseParagraphDisposition(
                    self.right.paragraphs[3].id, ParagraphDispositionReason.METADATA,
                    after_alignment_unit=3,
                ),
            ),
        )

    def save(self, gold: PairwiseGold | None = None) -> Path:
        path = self.paths.pairwise_gold_chapter(self.left.source, self.right.source, 1)
        save_pairwise_gold(path, gold or self.draft)
        return path

    def proposal(self, gold: PairwiseGold, *, complete: bool = False) -> PairwiseAlignmentProposal:
        def side(value: PairwiseAlignmentSide) -> ProposalSide:
            return ProposalSide(unmatched=True) if value.gap else ProposalSide(paragraphs=value.paragraphs)
        units = [
            ProposalUnit(proposal_unit_id(index), side(item.left), side(item.right))
            for index, item in enumerate(gold.alignment_units, start=1)
        ]
        if complete:
            for item in gold.paragraph_dispositions:
                paragraph = ProposalSide(paragraphs=(item.paragraph_id,))
                units.append(ProposalUnit(
                    proposal_unit_id(len(units) + 1),
                    paragraph if item.paragraph_id.source_id == self.left.source else ProposalSide(unmatched=True),
                    paragraph if item.paragraph_id.source_id == self.right.source else ProposalSide(unmatched=True),
                ))
        params = {"mode": "synthetic"}
        return PairwiseAlignmentProposal(
            PAIRWISE_ALIGNMENT_PROPOSAL_SCHEMA_VERSION,
            PAIRWISE_ALIGNMENT_PROPOSAL_ARTIFACT_TYPE, "lotm", 1,
            AlignmentDirection(gold.direction.value),
            CoverageMode.COMPLETE if complete else CoverageMode.PARTIAL,
            ProposalSourceSnapshot(
                gold.left_source.source_id, gold.left_source.language,
                gold.left_source.normalized_path, gold.left_source.normalized_sha256,
                gold.left_source.normalized_schema_version, gold.left_source.paragraph_count,
            ),
            ProposalSourceSnapshot(
                gold.right_source.source_id, gold.right_source.language,
                gold.right_source.normalized_path, gold.right_source.normalized_sha256,
                gold.right_source.normalized_schema_version, gold.right_source.paragraph_count,
            ),
            ProducerProvenance("synthetic", "1", params, parameters_sha256(params), "deadbeef"),
            tuple(units),
        )


class PairwiseGoldContractTests(unittest.TestCase):
    def test_pairwise_gold_dependency_boundary_and_evaluator_has_no_trilingual_projection(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for path in (root / "src/lotm_v2/gold/pairwise").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            modules = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
            modules += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
            self.assertFalse(any("alignment" in module.split(".") for module in modules), path)
            self.assertFalse(any("pairwise_review" in module.split(".") for module in modules), path)
        evaluation = (root / "src/lotm_v2/alignment/evaluation.py").read_text(encoding="utf-8")
        self.assertNotIn("gold_projection", evaluation)
        self.assertNotIn("load_fully_validated_gold", evaluation)

    def test_strict_round_trip_hashes_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = PairwiseFixture(Path(directory)); gold = f.complete()
            path = f.save(gold)
            self.assertEqual(load_pairwise_gold(path), gold)
            self.assertEqual(pairwise_gold_from_dict(pairwise_gold_to_dict(gold)), gold)
            self.assertNotEqual(pairwise_gold_document_sha256(gold), pairwise_gold_file_sha256(path))
            self.assertEqual(
                pairwise_gold_document_sha256(gold),
                pairwise_gold_document_sha256(pairwise_gold_from_dict(json.loads(json.dumps(pairwise_gold_to_dict(gold), sort_keys=True)))),
            )
            payload = pairwise_gold_to_dict(gold); payload["unexpected"] = True
            with self.assertRaisesRegex(ValueError, "unknown fields"):
                pairwise_gold_from_dict(payload)
            self.assertEqual(gold.status, PairwiseGoldStatus.DRAFT)

    def test_directions_exact_sources_gap_and_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = PairwiseFixture(Path(directory)); gold = f.complete(PairwiseGoldStatus.CONFIRMED)
            load_and_validate_pairwise_gold(gold, f.root)
            self.assertIn("zh--en", gold.alignment_units[0].id)
            with self.assertRaisesRegex(ValueError, "GAP on both"):
                PairwiseAlignmentUnit(
                    "x", PairwiseAlignmentSide(gap=PairwiseGap(GoldFlag.OMISSION)),
                    PairwiseAlignmentSide(gap=PairwiseGap(GoldFlag.ADDITION)),
                )
            with self.assertRaisesRegex(ValueError, "right language"):
                replace(gold, direction=PairwiseDirection.ZH_RU)
            with self.assertRaisesRegex(ValueError, "source IDs must differ"):
                replace(gold, right_source=replace(gold.right_source, source_id=gold.left_source.source_id))

    def test_validation_shapes_contiguity_crossing_duplicates_and_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = PairwiseFixture(Path(directory)); base = f.complete()
            validate_pairwise_gold(base, f.left, f.right, sha256(f.left_path), sha256(f.right_path))
            shapes = (
                (f.unit(1, (1,), (1, 2)), f.unit(2, (2, 3), (3,)), f.unit(3, (4,), (4,))),
                (f.unit(1, (1, 2), (1, 2)), f.unit(2, (3, 4), (3, 4))),
            )
            for units in shapes:
                validate_pairwise_gold(
                    replace(base, alignment_units=units, paragraph_dispositions=()),
                    f.left, f.right, sha256(f.left_path), sha256(f.right_path),
                )
            hole = replace(base, alignment_units=(f.unit(1, (1, 3), (1,)), *base.alignment_units[1:]))
            with self.assertRaisesRegex(PairwiseGoldValidationError, "Non-contiguous"):
                validate_pairwise_gold(hole, f.left, f.right, sha256(f.left_path), sha256(f.right_path))
            duplicate = replace(base, alignment_units=(f.unit(1, (1,), (1,)), f.unit(2, (1, 2, 3), (2,)), f.unit(3, (4,), (3,))))
            with self.assertRaisesRegex(PairwiseGoldValidationError, "used 2 times"):
                validate_pairwise_gold(duplicate, f.left, f.right, sha256(f.left_path), sha256(f.right_path))
            crossing = replace(base, alignment_units=(f.unit(1, (2,), (1,)), f.unit(2, (1, 3), (2,)), f.unit(3, (4,), (3,))))
            with self.assertRaisesRegex(PairwiseGoldValidationError, "crossing"):
                validate_pairwise_gold(crossing, f.left, f.right, sha256(f.left_path), sha256(f.right_path))
            missing = replace(base, paragraph_dispositions=())
            with self.assertRaisesRegex(PairwiseGoldValidationError, "Missing Pairwise Gold fate"):
                validate_pairwise_gold(missing, f.left, f.right, sha256(f.left_path), sha256(f.right_path))

    def test_disposition_conflicts_anchors_invalid_reference_and_same_language_ambiguity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = PairwiseFixture(Path(directory)); base = f.complete()
            with self.assertRaisesRegex(ValueError, "requires a note"):
                PairwiseParagraphDisposition(
                    f.right.paragraphs[-1].id,
                    ParagraphDispositionReason.OTHER,
                )
            both = replace(base, paragraph_dispositions=(
                PairwiseParagraphDisposition(f.left.paragraphs[0].id, ParagraphDispositionReason.METADATA),
                *base.paragraph_dispositions,
            ))
            with self.assertRaisesRegex(PairwiseGoldValidationError, "both aligned and dispositioned"):
                load_and_validate_pairwise_gold(both, f.root)
            duplicate = replace(base, paragraph_dispositions=base.paragraph_dispositions * 2)
            with self.assertRaisesRegex(PairwiseGoldValidationError, "dispositioned 2 times"):
                load_and_validate_pairwise_gold(duplicate, f.root)
            wrong_order = replace(base, paragraph_dispositions=(
                replace(base.paragraph_dispositions[0], after_alignment_unit=0),
            ))
            with self.assertRaisesRegex(PairwiseGoldValidationError, "Non-monotonic Pairwise Gold fate order"):
                load_and_validate_pairwise_gold(wrong_order, f.root)
            invalid_anchor = replace(base, paragraph_dispositions=(
                replace(base.paragraph_dispositions[0], after_alignment_unit=99),
            ))
            with self.assertRaisesRegex(PairwiseGoldValidationError, "anchor exceeds"):
                load_and_validate_pairwise_gold(invalid_anchor, f.root)
            invalid = replace(base, paragraph_dispositions=(
                PairwiseParagraphDisposition(
                    ParagraphId("lotm", f.right.source, 1, 999), ParagraphDispositionReason.METADATA,
                    after_alignment_unit=4,
                ),
            ))
            with self.assertRaisesRegex(PairwiseGoldValidationError, "Invalid Pairwise disposition"):
                load_and_validate_pairwise_gold(invalid, f.root)
            ambiguous = replace(base, right_source=replace(base.right_source, source_id=SourceId("en-other")))
            with self.assertRaisesRegex(ValueError, "canonical|exact source ID mismatch"):
                load_and_validate_pairwise_gold(ambiguous, f.root)

    def test_draft_creation_confirmation_hash_guard_and_active_session_guard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = PairwiseFixture(Path(directory)); path = f.save(f.complete())
            before = load_pairwise_gold(path)
            with self.assertRaisesRegex(ValueError, "SHA-256 changed"):
                confirm_pairwise_gold(path, f.root, "0" * 64, sha256(path))
            with self.assertRaisesRegex(ValueError, "file SHA-256 changed"):
                confirm_pairwise_gold(
                    path, f.root, pairwise_gold_document_sha256(before), "0" * 64,
                )
            self.assertEqual(load_pairwise_gold(path), before)
            active = f.paths.active_pairwise_review_session("lotm", f.left.source, f.right.source, 1)
            active.parent.mkdir(parents=True); active.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "active Pairwise Review Session"):
                confirm_pairwise_gold(
                    path, f.root, pairwise_gold_document_sha256(before), sha256(path),
                )
            active.unlink()
            confirmed = confirm_pairwise_gold(
                path, f.root, pairwise_gold_document_sha256(before), sha256(path),
            )
            self.assertEqual(confirmed.status, PairwiseGoldStatus.CONFIRMED)
            self.assertEqual(replace(confirmed, status=PairwiseGoldStatus.DRAFT), before)

    def test_schema_files_are_json_and_pairwise_gold_has_no_boundaries(self) -> None:
        root = Path(__file__).resolve().parents[1]
        schema = json.loads((root / "schemas/v2/pairwise-gold.schema.json").read_text(encoding="utf-8"))
        session = json.loads((root / "schemas/v2/pairwise-review-session.schema.json").read_text(encoding="utf-8"))
        self.assertFalse(schema["additionalProperties"])
        self.assertNotIn("boundaries", schema["properties"])
        self.assertEqual(session["properties"]["session_schema_version"]["const"], "1.1-draft")
        self.assertEqual(session["properties"]["review_semantics_version"]["const"], "1.1")
        for field in ("baseline_gold", "revision", "journal", "request_history"):
            self.assertIn(field, session["required"])
        self.assertEqual(session["properties"]["working_gold"]["$ref"], "pairwise-gold.schema.json")

    def test_root_confined_canonical_paths_reject_windows_and_escape_forms(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); paths = PathPolicy(root)
            canonical = "data/normalized/v2/zh/ch_0001.json"
            self.assertEqual(
                paths.resolve_repository_relative(canonical),
                paths.normalized_chapter(SourceId("zh"), 1).resolve(),
            )
            for value in (
                "../outside.json", "C:/outside.json", "C:outside.json",
                "//server/share/outside.json", "data\\normalized/v2/zh/ch_0001.json",
                "data/normalized/../outside.json",
            ):
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, "canonical|absolute|drive|escapes"):
                    paths.resolve_repository_relative(value)
            if os.name == "nt":
                self.assertEqual(
                    paths.require_normalized_chapter_path(
                        canonical.upper(), SourceId("zh"), 1,
                    ),
                    paths.normalized_chapter(SourceId("zh"), 1).resolve(),
                )


    def test_symlink_or_junction_escape_is_rejected_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside_directory:
            root = Path(directory); paths = PathPolicy(root); outside = Path(outside_directory)
            link = root / "linked-outside"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlink/junction containment execution unavailable: {error}")
            with self.assertRaisesRegex(ValueError, "escapes project root"):
                paths.resolve_repository_relative("linked-outside/ch_0001.json")

    def test_noncanonical_pairwise_gold_creation_and_confirmation_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = PairwiseFixture(Path(directory)); wrong = f.root / "pairwise.json"
            with self.assertRaises(SystemExit):
                cli_main([
                    "pairwise-gold-draft", str(f.left_path), str(f.right_path),
                    "--direction", "zh-en", "--output", str(wrong),
                    "--root", str(f.root),
                ])
            complete = f.complete(); save_pairwise_gold(wrong, complete)
            with self.assertRaisesRegex(ValueError, "canonical path"):
                confirm_pairwise_gold(
                    wrong, f.root, pairwise_gold_document_sha256(complete), sha256(wrong),
                )

        with tempfile.TemporaryDirectory() as directory:
            f = PairwiseFixture(Path(directory), PairwiseDirection.ZH_RU)
            complete = f.complete()
            wrong_pair = f.root / "data/gold/v2/pairwise/zh--ru/ch_0001.json"
            save_pairwise_gold(wrong_pair, complete)
            with self.assertRaisesRegex(ValueError, "canonical path"):
                confirm_pairwise_gold(
                    wrong_pair, f.root, pairwise_gold_document_sha256(complete),
                    sha256(wrong_pair),
                )

    def test_proposal_and_gold_stored_source_references_use_confined_resolver(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = PairwiseFixture(Path(directory)); gold = f.complete()
            proposal = f.proposal(gold)
            for value in (
                "../outside.json", "C:/outside.json", "C:outside.json",
                "//server/share/outside.json", "data\\normalized/v2/zh/ch_0001.json",
            ):
                with self.subTest(kind="proposal", value=value), self.assertRaises(ValueError):
                    load_and_validate_proposal(
                        replace(
                            proposal,
                            left_source=replace(proposal.left_source, normalized_path=value),
                        ),
                        f.root,
                    )
                with self.subTest(kind="gold", value=value), self.assertRaises(ValueError):
                    load_and_validate_pairwise_gold(
                        replace(
                            gold,
                            left_source=replace(gold.left_source, normalized_path=value),
                        ),
                        f.root,
                    )


class BootstrapAndTrilingualContiguityTests(unittest.TestCase):
    def _trilingual(self, root: Path) -> tuple[Path, tuple[Chapter, ...], GoldChapter]:
        paths = PathPolicy(root)
        zh, en, ru = make_chapter(Language.ZH, "zh", 3), make_chapter(Language.EN, "en", 5), make_chapter(Language.RU, "ru-official", 5)
        chapters = (zh, en, ru); refs = []
        for chapter in chapters:
            path = paths.normalized_chapter(chapter.source, 1); save_chapter(path, chapter)
            refs.append(GoldSourceRef(chapter.source, chapter.language, paths.relative(path), sha256(path)))
        gap = lambda reason: GoldAlignmentSide(gap=GoldGap(reason))
        units = (
            GoldAlignmentUnit(alignment_unit_id(zh.id, 1), gside(zh, 1, 2), gside(en, 1, 2), gside(ru, 1)),
            GoldAlignmentUnit(alignment_unit_id(zh.id, 2), gside(zh, 3), gap(GoldFlag.OMISSION), gside(ru, 2)),
            GoldAlignmentUnit(alignment_unit_id(zh.id, 3), gap(GoldFlag.ADDITION), gside(en, 3), gside(ru, 3)),
            GoldAlignmentUnit(alignment_unit_id(zh.id, 4), gap(GoldFlag.ADDITION), gap(GoldFlag.ADDITION), gside(ru, 4)),
            GoldAlignmentUnit(alignment_unit_id(zh.id, 5), gap(GoldFlag.ADDITION), gside(en, 4), gap(GoldFlag.ADDITION)),
        )
        boundaries = tuple(GoldBoundary(item.id, BoundaryDecision.JOIN) for item in units[:-1])
        dispositions = (
            ParagraphDisposition(en.paragraphs[4].id, ParagraphDispositionReason.METADATA, after_alignment_unit=5),
            ParagraphDisposition(ru.paragraphs[4].id, ParagraphDispositionReason.METADATA, after_alignment_unit=5),
        )
        gold = GoldChapter("1.0-draft", GoldStatus.DRAFT, zh.id, tuple(refs), units, boundaries, None, dispositions)
        validate_gold_chapter(gold, chapters)
        path = paths.gold_chapter(1); save_gold(path, gold)
        return path, chapters, gold

    def test_bootstrap_is_read_only_coarse_gap_out_scope_disposition_and_hash_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path, _, gold = self._trilingual(root); before = path.read_bytes()
            result = inspect_trilingual_bootstrap(path, root, "zh", "en")
            self.assertFalse(result["authoritative"])
            self.assertIn("NOT PAIRWISE TRUTH", result["label"])
            kinds = [item["kind"] for item in result["items"]]
            self.assertIn("coarse_region", kinds); self.assertIn("gap_suggestion", kinds)
            self.assertIn("out_of_pair_scope", kinds); self.assertIn("disposition_suggestion", kinds)
            self.assertTrue(all(not item["confirmed_pairwise_truth"] for item in result["items"]))
            self.assertEqual(result["trilingual_gold_document_sha256"], gold_document_sha256(gold))
            self.assertEqual(path.read_bytes(), before)
            with self.assertRaisesRegex(ValueError, "exact declared source ID"):
                inspect_trilingual_bootstrap(path, root, "zh", "fan-75")

    def test_legacy_trilingual_non_contiguous_side_remains_compatible_and_current_chapter_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); _, chapters, gold = self._trilingual(root)
            legacy = replace(
                gold,
                alignment_units=(
                    replace(gold.alignment_units[0], en=gside(chapters[1], 1, 3)),
                    gold.alignment_units[1],
                    replace(
                        gold.alignment_units[2],
                        en=GoldAlignmentSide(gap=GoldGap(GoldFlag.EDITION_DIFFERENCE)),
                    ),
                    *gold.alignment_units[3:],
                ),
                paragraph_dispositions=(
                    ParagraphDisposition(
                        chapters[1].paragraphs[1].id,
                        ParagraphDispositionReason.METADATA,
                        after_alignment_unit=0,
                    ),
                    *gold.paragraph_dispositions,
                ),
            )
            validate_gold_chapter(legacy, chapters)
        root = Path(__file__).resolve().parents[1]
        path = root / "data/gold/v2/ch_0001.json"; before = path.read_bytes()
        from src.lotm_v2.alignment.gold_projection import load_fully_validated_gold
        gold, chapters = load_fully_validated_gold(path, root)
        validate_gold_chapter(gold, chapters)
        self.assertEqual(path.read_bytes(), before)

    def test_current_chapter_bootstrap_reports_known_coarse_regions(self) -> None:
        root = Path(__file__).resolve().parents[1]
        path = root / "data/gold/v2/ch_0001.json"
        zh_en = inspect_trilingual_bootstrap(path, root, "zh", "en")
        zh_ru = inspect_trilingual_bootstrap(path, root, "zh", "ru-official")
        by_id = {item.get("trilingual_unit_id"): item for item in zh_en["items"]}
        self.assertEqual(by_id["lotm:0001:a000022"]["left"]["paragraphs"], [
            "lotm:zh:0001:p000038", "lotm:zh:0001:p000039", "lotm:zh:0001:p000040",
        ])
        self.assertEqual(by_id["lotm:0001:a000039"]["right"]["paragraphs"], [
            "lotm:en:0001:p000057", "lotm:en:0001:p000058", "lotm:en:0001:p000059",
        ])
        self.assertTrue(zh_ru["items"])


class PairwiseEvaluatorTests(unittest.TestCase):
    def test_evaluator_hashes_the_exact_proposal_and_gold_bytes_it_parsed(self) -> None:
        from src.lotm_v2.alignment import evaluation as evaluation_module

        with tempfile.TemporaryDirectory() as directory:
            f = PairwiseFixture(Path(directory))
            gold = f.complete(PairwiseGoldStatus.CONFIRMED)
            proposal = f.proposal(gold, complete=True)
            proposal_path = f.root / "external-proposal.json"; save_proposal(proposal_path, proposal)
            gold_path = f.save(gold)
            proposal_bytes, gold_bytes = proposal_path.read_bytes(), gold_path.read_bytes()
            original_parser = evaluation_module.proposal_from_json_bytes

            def replace_paths_after_snapshot(data: bytes, context: str):
                proposal_path.write_text("{}\n", encoding="utf-8")
                gold_path.write_text("{}\n", encoding="utf-8")
                return original_parser(data, context)

            with patch(
                "src.lotm_v2.alignment.evaluation.proposal_from_json_bytes",
                side_effect=replace_paths_after_snapshot,
            ):
                result = evaluate_proposal_files(proposal_path, gold_path, f.root)
            self.assertEqual(
                result["proposal"]["file_sha256"], hashlib.sha256(proposal_bytes).hexdigest(),
            )
            self.assertEqual(
                result["pairwise_gold"]["file_sha256"], hashlib.sha256(gold_bytes).hexdigest(),
            )
            self.assertNotEqual(result["proposal"]["file_sha256"], sha256(proposal_path))
            self.assertNotEqual(result["pairwise_gold"]["file_sha256"], sha256(gold_path))

    def test_confirmed_default_draft_opt_in_identity_and_disposition_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = PairwiseFixture(Path(directory)); draft = f.complete(); proposal = f.proposal(draft, complete=True)
            proposal_path = f.root / "proposal.json"; save_proposal(proposal_path, proposal)
            gold_path = f.save(draft)
            with self.assertRaisesRegex(ValueError, "confirmed Pairwise Gold"):
                evaluate_proposal_files(proposal_path, gold_path, f.root)
            result = evaluate_proposal_files(proposal_path, gold_path, f.root, allow_draft_gold=True)
            self.assertEqual(result["benchmark_authority"], "development_draft")
            self.assertEqual(result["exact_units"], {
                "matches": 3, "total_proposal_unit_count": 4,
                "scored_candidate_unit_count": 3, "gold_count": 3,
                "precision": 1.0, "recall": 1.0, "f1": 1.0,
            })
            self.assertEqual(result["dispositions"]["disposition_intrusion_unit_count"], 0)
            self.assertEqual(result["coverage"]["right"]["candidate_source_coverage"], 1.0)
            self.assertEqual(result["coverage"]["right"]["gold_alignable_coverage"], 1.0)
            self.assertEqual(result["proposal"]["document_sha256"], proposal_document_sha256(proposal))
            self.assertEqual(result["proposal"]["file_sha256"], sha256(proposal_path))
            self.assertEqual(result["pairwise_gold"]["file_sha256"], sha256(gold_path))
            self.assertEqual(result["proposal"]["parameters_sha256"], proposal.producer.parameters_sha256)

    def test_trilingual_rejected_and_different_revisions_distinguishable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = PairwiseFixture(Path(directory)); gold = f.complete(PairwiseGoldStatus.CONFIRMED)
            proposal = f.proposal(gold); proposal_path = f.root / "proposal.json"; save_proposal(proposal_path, proposal)
            tri = GoldChapter("1.0-draft", GoldStatus.DRAFT, f.left.id, ())
            tri_path = f.root / "tri.json"; save_gold(tri_path, tri)
            with self.assertRaisesRegex(ValueError, "trilingual Gold is not accepted"):
                evaluate_proposal_files(proposal_path, tri_path, f.root)
            first = load_and_validate_pairwise_gold(gold, f.root)
            first_result = evaluate_validated_proposal(load_and_validate_proposal(proposal, f.root), first)
            changed = replace(gold, notes="different human revision")
            second_result = evaluate_validated_proposal(
                load_and_validate_proposal(proposal, f.root), load_and_validate_pairwise_gold(changed, f.root),
            )
            self.assertEqual(first_result["exact_units"], second_result["exact_units"])
            self.assertNotEqual(
                first_result["pairwise_gold"]["document_sha256"], second_result["pairwise_gold"]["document_sha256"],
            )

    def test_coarse_third_language_cannot_change_zh_en_or_zh_ru_perfect_score(self) -> None:
        for direction in (PairwiseDirection.ZH_EN, PairwiseDirection.ZH_RU):
            with self.subTest(direction=direction), tempfile.TemporaryDirectory() as directory:
                f = PairwiseFixture(Path(directory), direction, left_count=2, right_count=2)
                gold = replace(
                    f.draft, status=PairwiseGoldStatus.CONFIRMED,
                    alignment_units=(f.unit(1, (1,), (1,)), f.unit(2, (2,), (2,))),
                )
                proposal = f.proposal(gold)
                baseline = evaluate_validated_proposal(
                    load_and_validate_proposal(proposal, f.root),
                    load_and_validate_pairwise_gold(gold, f.root),
                )
                third_language = Language.RU if direction is PairwiseDirection.ZH_EN else Language.EN
                third_id = "ru-official" if third_language is Language.RU else "en"
                third = make_chapter(third_language, third_id, 2)
                third_path = f.paths.normalized_chapter(third.source, 1); save_chapter(third_path, third)
                en = f.right if direction is PairwiseDirection.ZH_EN else third
                ru = third if direction is PairwiseDirection.ZH_EN else f.right
                paths_by_source = {f.left.source: f.left_path, f.right.source: f.right_path, third.source: third_path}
                refs = tuple(
                    GoldSourceRef(item.source, item.language, f.paths.relative(paths_by_source[item.source]), sha256(paths_by_source[item.source]))
                    for item in (f.left, en, ru)
                )
                coarse = GoldAlignmentUnit(
                    alignment_unit_id(f.left.id, 1), gside(f.left, 1, 2), gside(en, 1, 2), gside(ru, 1, 2),
                )
                tri_path = f.root / "trilingual-bootstrap.json"
                save_gold(tri_path, GoldChapter("1.0-draft", GoldStatus.DRAFT, f.left.id, refs, (coarse,), ()))
                split_units = tuple(
                    GoldAlignmentUnit(
                        alignment_unit_id(f.left.id, index), gside(f.left, index),
                        gside(en, index), gside(ru, index),
                    ) for index in (1, 2)
                )
                save_gold(tri_path, GoldChapter(
                    "1.0-draft", GoldStatus.DRAFT, f.left.id, refs, split_units,
                    (GoldBoundary(split_units[0].id, BoundaryDecision.JOIN),),
                ))
                after_third_language_regrouping = evaluate_validated_proposal(
                    load_and_validate_proposal(proposal, f.root),
                    load_and_validate_pairwise_gold(gold, f.root),
                )
                self.assertEqual(baseline["exact_units"]["f1"], 1.0)
                self.assertEqual(baseline, after_third_language_regrouping)
                self.assertNotIn("third", baseline["sources"])

    def test_cli_draft_validate_confirm_and_development_evaluate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = PairwiseFixture(Path(directory)); path = f.paths.pairwise_gold_chapter(
                f.left.source, f.right.source, 1,
            )
            with redirect_stdout(io.StringIO()):
                self.assertEqual(cli_main([
                    "pairwise-gold-draft", str(f.left_path), str(f.right_path),
                    "--direction", "zh-en", "--output", str(path), "--root", str(f.root),
                ]), 0)
            replace_pairwise_gold_atomic(path, f.complete())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(cli_main(["pairwise-gold-validate", str(path), "--root", str(f.root)]), 0)
                self.assertEqual(cli_main([
                    "pairwise-gold-confirm", str(path), "--expected-document-sha256",
                    pairwise_gold_document_sha256(load_pairwise_gold(path)),
                    "--expected-file-sha256", sha256(path), "--root", str(f.root),
                ]), 0)


if __name__ == "__main__":
    unittest.main()
