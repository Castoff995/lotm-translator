from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
import ast
import hashlib
import io
import json
import math
import tempfile
import unittest
from pathlib import Path

from src.lotm_v2.alignment.evaluation import evaluate_proposal_files, evaluate_validated_proposal
from src.lotm_v2.alignment.gold_projection import load_fully_validated_gold, project_validated_gold
from src.lotm_v2.alignment.io import (
    load_proposal, proposal_from_dict, proposal_to_dict, save_proposal,
)
from src.lotm_v2.alignment.model import (
    PAIRWISE_ALIGNMENT_PROPOSAL_ARTIFACT_TYPE, PAIRWISE_ALIGNMENT_PROPOSAL_SCHEMA_VERSION,
    AlignmentDirection, CoverageMode, PairwiseAlignmentProposal, ProducerProvenance,
    ProposalScore, ProposalSide, ProposalSourceSnapshot, ProposalUnit,
    parameters_sha256, proposal_unit_id,
)
from src.lotm_v2.alignment.validation import (
    ProposalValidationError, load_and_validate_proposal, validate_proposal,
)
from src.lotm_v2.cli import main as cli_main
from src.lotm_v2.domain import (
    Chapter, ChapterId, Language, Paragraph, ParagraphId, ParagraphType, SourceId,
)
from src.lotm_v2.gold.io import save_gold
from src.lotm_v2.gold.model import (
    BoundaryDecision, GoldAlignmentSide, GoldAlignmentUnit, GoldBoundary, GoldChapter,
    GoldFlag, GoldGap, GoldSourceRef, GoldStatus, ParagraphDisposition,
    ParagraphDispositionReason, alignment_unit_id,
)
from src.lotm_v2.gold.validation import validate_gold_chapter
from src.lotm_v2.gold.pairwise.io import save_pairwise_gold
from src.lotm_v2.gold.pairwise.model import (
    PAIRWISE_GOLD_ARTIFACT_TYPE, PAIRWISE_GOLD_SCHEMA_VERSION,
    PairwiseAlignmentSide, PairwiseAlignmentUnit, PairwiseCreationMode,
    PairwiseDirection, PairwiseGap, PairwiseGold, PairwiseGoldProvenance,
    PairwiseGoldSource, PairwiseGoldStatus, PairwiseParagraphDisposition,
    pairwise_alignment_unit_id,
)
from src.lotm_v2.gold.pairwise.validation import load_and_validate_pairwise_gold
from src.lotm_v2.infrastructure.corpus_io import save_chapter
from src.lotm_v2.infrastructure.paths import PathPolicy


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_chapter(language: Language, source_name: str, count: int, work: str = "lotm", number: int = 1) -> Chapter:
    source = SourceId(source_name)
    chapter_id = ChapterId(work, number)
    return Chapter(
        "2.2-draft", chapter_id, source, language,
        tuple(
            Paragraph(
                ParagraphId(work, source, number, index), source, language, chapter_id, index,
                f"raw {source_name} {index}", f"normalized {source_name} {index}", ParagraphType.STORY,
            )
            for index in range(1, count + 1)
        ),
    )


def paragraph_side(chapter: Chapter, *indices: int) -> ProposalSide:
    return ProposalSide(paragraphs=tuple(chapter.paragraphs[index - 1].id for index in indices))


def gold_side(chapter: Chapter, *indices: int) -> GoldAlignmentSide:
    return GoldAlignmentSide(paragraphs=tuple(chapter.paragraphs[index - 1].id for index in indices))


def unmatched() -> ProposalSide:
    return ProposalSide(unmatched=True)


def pairwise_benchmark_from_projection(projection, *, status=PairwiseGoldStatus.CONFIRMED) -> PairwiseGold:
    direction = PairwiseDirection(projection.direction.value)
    left_source = PairwiseGoldSource(
        projection.left_source.source_id, projection.left_source.language,
        projection.left_source.normalized_path, projection.left_source.normalized_sha256,
        projection.left_chapter.schema_version, len(projection.left_chapter.paragraphs),
    )
    right_source = PairwiseGoldSource(
        projection.right_source.source_id, projection.right_source.language,
        projection.right_source.normalized_path, projection.right_source.normalized_sha256,
        projection.right_chapter.schema_version, len(projection.right_chapter.paragraphs),
    )
    def side(value):
        return (
            PairwiseAlignmentSide(gap=PairwiseGap(GoldFlag.EDITION_DIFFERENCE))
            if value is None else
            PairwiseAlignmentSide(paragraphs=tuple(ParagraphId.parse(item) for item in value))
        )
    units = tuple(
        PairwiseAlignmentUnit(
            pairwise_alignment_unit_id(
                projection.gold.chapter, left_source.source_id, right_source.source_id, index,
            ), side(item.signature[0]), side(item.signature[1]),
        )
        for index, item in enumerate(projection.units, start=1)
    )
    dispositions = tuple(
        PairwiseParagraphDisposition(
            ParagraphId.parse(item), ParagraphDispositionReason.METADATA,
            after_alignment_unit=len(units),
        )
        for item in (*projection.left_disposition_ids, *projection.right_disposition_ids)
    )
    return PairwiseGold(
        PAIRWISE_GOLD_SCHEMA_VERSION, PAIRWISE_GOLD_ARTIFACT_TYPE, status,
        projection.gold.chapter.work_id, projection.gold.chapter.number, direction,
        left_source, right_source, units, dispositions,
        PairwiseGoldProvenance(PairwiseCreationMode.EMPTY),
    )


def validated_pairwise(projection, root: Path, *, status=PairwiseGoldStatus.CONFIRMED):
    return load_and_validate_pairwise_gold(
        pairwise_benchmark_from_projection(projection, status=status), root,
    )


class PairFixture:
    def __init__(self, root: Path, direction: AlignmentDirection = AlignmentDirection.ZH_EN, count: int = 7) -> None:
        self.root = root
        self.paths = PathPolicy(root)
        self.direction = direction
        self.left = make_chapter(Language.ZH, "zh", count)
        right_language = direction.right_language
        self.right = make_chapter(right_language, "en" if right_language is Language.EN else "ru-official", count)
        self.left_path = self.paths.normalized_chapter(self.left.source, 1)
        self.right_path = self.paths.normalized_chapter(self.right.source, 1)
        save_chapter(self.left_path, self.left); save_chapter(self.right_path, self.right)
        self.left_snapshot = self.snapshot(self.left, self.left_path)
        self.right_snapshot = self.snapshot(self.right, self.right_path)

    def snapshot(self, chapter: Chapter, path: Path) -> ProposalSourceSnapshot:
        return ProposalSourceSnapshot(
            chapter.source, chapter.language, self.paths.relative(path), sha256(path),
            chapter.schema_version, len(chapter.paragraphs),
        )

    def proposal(
        self, units: tuple[ProposalUnit, ...] = (), coverage: CoverageMode = CoverageMode.PARTIAL,
    ) -> PairwiseAlignmentProposal:
        parameters = {"threshold": 0.25, "window": 4}
        return PairwiseAlignmentProposal(
            PAIRWISE_ALIGNMENT_PROPOSAL_SCHEMA_VERSION,
            PAIRWISE_ALIGNMENT_PROPOSAL_ARTIFACT_TYPE,
            "lotm", 1, self.direction, coverage, self.left_snapshot, self.right_snapshot,
            ProducerProvenance("synthetic-producer", "0.1", parameters, parameters_sha256(parameters), None),
            units,
        )

    def validate(self, proposal: PairwiseAlignmentProposal) -> None:
        validate_proposal(proposal, self.left, self.right, sha256(self.left_path), sha256(self.right_path))


class GoldFixture:
    """Three aligned Paragraphs plus configurable dispositions on every source."""
    def __init__(self, root: Path, complex_units: bool = True, disposition_count: int = 1) -> None:
        self.root = root; self.paths = PathPolicy(root)
        paragraph_count = 3 + disposition_count
        self.zh = make_chapter(Language.ZH, "zh", paragraph_count)
        self.en = make_chapter(Language.EN, "en", paragraph_count)
        self.ru = make_chapter(Language.RU, "ru-official", paragraph_count)
        self.chapters = (self.zh, self.en, self.ru)
        self.chapter_paths = tuple(self.paths.normalized_chapter(item.source, 1) for item in self.chapters)
        for path, chapter in zip(self.chapter_paths, self.chapters): save_chapter(path, chapter)
        refs = tuple(
            GoldSourceRef(chapter.source, chapter.language, self.paths.relative(path), sha256(path))
            for chapter, path in zip(self.chapters, self.chapter_paths)
        )
        if complex_units:
            units = (
                GoldAlignmentUnit(alignment_unit_id(self.zh.id, 1), gold_side(self.zh, 1), gold_side(self.en, 1), gold_side(self.ru, 1)),
                GoldAlignmentUnit(
                    alignment_unit_id(self.zh.id, 2), gold_side(self.zh, 2),
                    GoldAlignmentSide(gap=GoldGap(GoldFlag.OMISSION)), gold_side(self.ru, 2),
                ),
                GoldAlignmentUnit(
                    alignment_unit_id(self.zh.id, 3), GoldAlignmentSide(gap=GoldGap(GoldFlag.ADDITION)),
                    gold_side(self.en, 2), GoldAlignmentSide(gap=GoldGap(GoldFlag.ADDITION)),
                ),
                GoldAlignmentUnit(alignment_unit_id(self.zh.id, 4), gold_side(self.zh, 3), gold_side(self.en, 3), gold_side(self.ru, 3)),
            )
        else:
            units = tuple(
                GoldAlignmentUnit(alignment_unit_id(self.zh.id, index), gold_side(self.zh, index), gold_side(self.en, index), gold_side(self.ru, index))
                for index in (1, 2, 3)
            )
        boundaries = tuple(
            GoldBoundary(unit.id, BoundaryDecision.JOIN if index % 2 else BoundaryDecision.BREAK)
            for index, unit in enumerate(units[:-1], start=1)
        )
        dispositions = tuple(
            ParagraphDisposition(paragraph.id, ParagraphDispositionReason.METADATA, after_alignment_unit=len(units))
            for chapter in self.chapters for paragraph in chapter.paragraphs[3:]
        )
        self.gold = GoldChapter(
            "1.0-draft", GoldStatus.DRAFT, self.zh.id, refs, units, boundaries, None, dispositions,
        )
        validate_gold_chapter(self.gold, self.chapters)
        self.gold_path = self.paths.gold_chapter(1); save_gold(self.gold_path, self.gold)

    def perfect(self, direction: AlignmentDirection) -> PairwiseAlignmentProposal:
        projection = project_validated_gold(self.gold, self.chapters, direction)
        parameters: dict[str, object] = {"mode": "test-only-gold-projection"}
        units = tuple(
            ProposalUnit(
                proposal_unit_id(index),
                unmatched() if projected.signature[0] is None else ProposalSide(
                    paragraphs=tuple(ParagraphId.parse(item) for item in projected.signature[0])
                ),
                unmatched() if projected.signature[1] is None else ProposalSide(
                    paragraphs=tuple(ParagraphId.parse(item) for item in projected.signature[1])
                ),
            )
            for index, projected in enumerate(projection.units, start=1)
        )
        right_chapter = self.en if direction is AlignmentDirection.ZH_EN else self.ru
        right_ref = next(item for item in self.gold.sources if item.language is direction.right_language)
        left_ref = next(item for item in self.gold.sources if item.language is Language.ZH)
        return PairwiseAlignmentProposal(
            PAIRWISE_ALIGNMENT_PROPOSAL_SCHEMA_VERSION, PAIRWISE_ALIGNMENT_PROPOSAL_ARTIFACT_TYPE,
            "lotm", 1, direction, CoverageMode.PARTIAL,
            ProposalSourceSnapshot(self.zh.source, Language.ZH, left_ref.normalized_path, left_ref.normalized_sha256, self.zh.schema_version, len(self.zh.paragraphs)),
            ProposalSourceSnapshot(right_chapter.source, direction.right_language, right_ref.normalized_path, right_ref.normalized_sha256, right_chapter.schema_version, len(right_chapter.paragraphs)),
            ProducerProvenance("test-only", "1", parameters, parameters_sha256(parameters)), units,
        )

    def complete(self, direction: AlignmentDirection, group_dispositions: bool = False) -> PairwiseAlignmentProposal:
        proposal = self.perfect(direction)
        projection = project_validated_gold(self.gold, self.chapters, direction)
        units = list(proposal.units)
        for side_name, disposition_ids in (
            ("left", projection.left_disposition_ids), ("right", projection.right_disposition_ids),
        ):
            groups = (disposition_ids,) if group_dispositions and disposition_ids else tuple((item,) for item in disposition_ids)
            for group in groups:
                paragraph_side_value = ProposalSide(paragraphs=tuple(ParagraphId.parse(item) for item in group))
                left, right = (
                    (paragraph_side_value, unmatched()) if side_name == "left" else (unmatched(), paragraph_side_value)
                )
                units.append(ProposalUnit(proposal_unit_id(len(units) + 1), left, right))
        return replace(proposal, coverage_mode=CoverageMode.COMPLETE, units=tuple(units))


class ProposalArtifactTests(unittest.TestCase):
    def test_round_trip_strict_codec_and_no_source_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PairFixture(Path(directory))
            proposal = fixture.proposal((ProposalUnit(
                "u000001", paragraph_side(fixture.left, 1), paragraph_side(fixture.right, 1),
                (ProposalScore("distance", -2.5, "lower is better; unbounded"),),
            ),))
            path = Path(directory) / "proposal.json"; save_proposal(path, proposal)
            self.assertEqual(load_proposal(path), proposal)
            payload = proposal_to_dict(proposal)
            for context, mutate in (
                ("top", lambda p: p.update({"normalized_text": "forbidden"})),
                ("source", lambda p: p["left_source"].update({"raw_text": "forbidden"})),
                ("unit", lambda p: p["units"][0].update({"translation": "forbidden"})),
            ):
                broken = json.loads(json.dumps(payload)); mutate(broken)
                with self.subTest(context=context), self.assertRaisesRegex(ValueError, "unknown fields"):
                    proposal_from_dict(broken)

    def test_canonical_parameter_hash(self) -> None:
        self.assertEqual(parameters_sha256({"b": 2, "a": 1}), parameters_sha256({"a": 1, "b": 2}))
        with self.assertRaisesRegex(ValueError, "parameters_sha256"):
            ProducerProvenance("p", "1", {"a": 1}, "0" * 64)

    def test_score_contract_accepts_finite_without_unit_interval(self) -> None:
        score = ProposalScore("distance", -17.5, "lower is better; unbounded")
        self.assertEqual(score.value, -17.5)
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "finite"):
                ProposalScore("x", value, "synthetic")

    def test_strict_side_and_direction_rejections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PairFixture(Path(directory))
            for direction in ("en-zh", "ru-zh", "en-ru", "ru-en"):
                payload = proposal_to_dict(fixture.proposal())
                payload["direction"] = direction
                with self.subTest(direction=direction), self.assertRaises(ValueError):
                    proposal_from_dict(payload)
            payload = proposal_to_dict(fixture.proposal())
            payload["units"] = [{"id": "u000001", "left": {"unmatched": True}, "right": {"unmatched": True}}]
            with self.assertRaisesRegex(ValueError, "both sides unmatched"):
                proposal_from_dict(payload)
            payload["units"][0]["left"] = {"paragraphs": [], "unmatched": True}
            with self.assertRaisesRegex(ValueError, "exactly paragraphs"):
                proposal_from_dict(payload)


class ProposalStructuralValidationTests(unittest.TestCase):
    def test_all_supported_shapes_validate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = PairFixture(Path(directory), count=7)
            units = (
                ProposalUnit("u000001", paragraph_side(f.left, 1), paragraph_side(f.right, 1)),
                ProposalUnit("u000002", paragraph_side(f.left, 2), paragraph_side(f.right, 2, 3)),
                ProposalUnit("u000003", paragraph_side(f.left, 3, 4), paragraph_side(f.right, 4)),
                ProposalUnit("u000004", paragraph_side(f.left, 5, 6), paragraph_side(f.right, 5, 6)),
                ProposalUnit("u000005", paragraph_side(f.left, 7), unmatched()),
                ProposalUnit("u000006", unmatched(), paragraph_side(f.right, 7)),
            )
            f.validate(f.proposal(units))

    def test_sequential_unit_ids_and_identity_snapshot_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = PairFixture(Path(directory), count=2)
            bad_id = f.proposal((ProposalUnit("u000002", paragraph_side(f.left, 1), paragraph_side(f.right, 1)),))
            with self.assertRaisesRegex(ProposalValidationError, "Expected proposal unit ID"):
                f.validate(bad_id)
            duplicate_ids = f.proposal((
                ProposalUnit("u000001", paragraph_side(f.left, 1), paragraph_side(f.right, 1)),
                ProposalUnit("u000001", paragraph_side(f.left, 2), paragraph_side(f.right, 2)),
            ))
            with self.assertRaisesRegex(ProposalValidationError, "Duplicate proposal unit ID"):
                f.validate(duplicate_ids)
            cases = (
                replace(f.proposal(), work_id="other"),
                replace(f.proposal(), chapter=2),
                replace(f.proposal(), left_source=replace(f.left_snapshot, normalized_sha256="b" * 64)),
                replace(f.proposal(), left_source=replace(f.left_snapshot, normalized_schema_version="wrong")),
                replace(f.proposal(), left_source=replace(f.left_snapshot, paragraph_count=999)),
                replace(f.proposal(), left_source=replace(f.left_snapshot, language=Language.EN)),
                replace(f.proposal(), left_source=replace(f.left_snapshot, source_id=SourceId("other"))),
            )
            for proposal in cases:
                with self.subTest(proposal=proposal), self.assertRaises(ProposalValidationError): f.validate(proposal)

    def test_reference_source_unknown_duplicate_and_order_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = PairFixture(Path(directory), count=3)
            unknown = ProposalSide(paragraphs=(ParagraphId("lotm", f.left.source, 1, 999),))
            wrong_source = ProposalSide(paragraphs=(f.right.paragraphs[0].id,))
            invalid_units = (
                (ProposalUnit("u000001", unknown, paragraph_side(f.right, 1)), "Unknown"),
                (ProposalUnit("u000001", wrong_source, paragraph_side(f.right, 1)), "wrong source"),
                (ProposalUnit("u000001", paragraph_side(f.left, 2, 1), paragraph_side(f.right, 1)), "inside"),
            )
            for unit, message in invalid_units:
                with self.subTest(message=message), self.assertRaisesRegex(ProposalValidationError, message):
                    f.validate(f.proposal((unit,)))
            duplicate_inside = ProposalUnit(
                "u000001", ProposalSide(paragraphs=(f.left.paragraphs[0].id, f.left.paragraphs[0].id)),
                paragraph_side(f.right, 1),
            )
            with self.assertRaisesRegex(ProposalValidationError, "Duplicate Paragraph inside"):
                f.validate(f.proposal((duplicate_inside,)))
            duplicate_across = (
                ProposalUnit("u000001", paragraph_side(f.left, 1), paragraph_side(f.right, 1)),
                ProposalUnit("u000002", paragraph_side(f.left, 1), paragraph_side(f.right, 2)),
            )
            with self.assertRaisesRegex(ProposalValidationError, "used 2 times"):
                f.validate(f.proposal(duplicate_across))
            crossing = (
                ProposalUnit("u000001", paragraph_side(f.left, 2), paragraph_side(f.right, 1)),
                ProposalUnit("u000002", paragraph_side(f.left, 1), paragraph_side(f.right, 2)),
            )
            with self.assertRaisesRegex(ProposalValidationError, "crossing"):
                f.validate(f.proposal(crossing))

    def test_fake_sentence_or_offset_id_is_not_a_paragraph(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = PairFixture(Path(directory), count=1)
            payload = proposal_to_dict(f.proposal())
            payload["units"] = [{
                "id": "u000001", "left": {"paragraphs": ["lotm:zh:0001:s000001"]},
                "right": {"paragraphs": [str(f.right.paragraphs[0].id)]},
            }]
            with self.assertRaisesRegex(ValueError, "Invalid paragraph id"):
                proposal_from_dict(payload)

    def test_partial_skip_valid_complete_skip_rejected_and_complete_exact_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = PairFixture(Path(directory), count=3)
            partial_units = (ProposalUnit("u000001", paragraph_side(f.left, 2), paragraph_side(f.right, 2)),)
            f.validate(f.proposal(partial_units, CoverageMode.PARTIAL))
            with self.assertRaisesRegex(ProposalValidationError, "Complete proposal skips"):
                f.validate(f.proposal(partial_units, CoverageMode.COMPLETE))
            complete_units = (
                ProposalUnit("u000001", paragraph_side(f.left, 1), paragraph_side(f.right, 1)),
                ProposalUnit("u000002", paragraph_side(f.left, 2), unmatched()),
                ProposalUnit("u000003", paragraph_side(f.left, 3), paragraph_side(f.right, 2)),
                ProposalUnit("u000004", unmatched(), paragraph_side(f.right, 3)),
            )
            f.validate(f.proposal(complete_units, CoverageMode.COMPLETE))

    def test_contiguity_rejects_hole_inside_unit_but_allows_skip_between_partial_units(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = PairFixture(Path(directory), count=3)
            hole = (ProposalUnit(
                "u000001", paragraph_side(f.left, 1, 3), paragraph_side(f.right, 1),
            ),)
            with self.assertRaisesRegex(ProposalValidationError, "Non-contiguous"):
                f.validate(f.proposal(hole, CoverageMode.PARTIAL))
            skipped_between = (
                ProposalUnit("u000001", paragraph_side(f.left, 1), paragraph_side(f.right, 1)),
                ProposalUnit("u000002", paragraph_side(f.left, 3), paragraph_side(f.right, 3)),
            )
            f.validate(f.proposal(skipped_between, CoverageMode.PARTIAL))


class GoldProjectionAndEvaluationTests(unittest.TestCase):
    def _validated(self, fixture: GoldFixture, proposal: PairwiseAlignmentProposal):
        return load_and_validate_proposal(proposal, fixture.root)

    def test_gold_pair_projections_gaps_out_of_scope_and_dispositions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = GoldFixture(Path(directory))
            zh_en = project_validated_gold(f.gold, f.chapters, AlignmentDirection.ZH_EN)
            zh_ru = project_validated_gold(f.gold, f.chapters, AlignmentDirection.ZH_RU)
            self.assertEqual((len(zh_en.units), zh_en.out_of_pair_scope_gold_unit_count), (4, 0))
            self.assertEqual((len(zh_ru.units), zh_ru.out_of_pair_scope_gold_unit_count), (3, 1))
            self.assertIsNone(zh_en.units[1].signature[1])
            self.assertIsNone(zh_en.units[2].signature[0])
            self.assertEqual(len(zh_en.left_disposition_ids), 1)
            self.assertNotIn(str(f.zh.paragraphs[3].id), {item for unit in zh_en.units for side in unit.signature for item in (side or ())})
            with self.assertRaisesRegex(ValueError, "Unsupported"):
                project_validated_gold(f.gold, f.chapters, "en-ru")  # type: ignore[arg-type]

    def test_gold_gap_reasons_and_join_break_do_not_change_projection_or_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = GoldFixture(Path(directory))
            direction = AlignmentDirection.ZH_EN
            baseline = project_validated_gold(f.gold, f.chapters, direction)
            unit = f.gold.alignment_units[1]
            changed_unit = replace(unit, en=GoldAlignmentSide(gap=GoldGap(GoldFlag.TRANSLATOR_NOTE, "changed")))
            changed_boundaries = tuple(
                replace(item, decision=BoundaryDecision.BREAK if item.decision is BoundaryDecision.JOIN else BoundaryDecision.JOIN)
                for item in f.gold.boundaries
            )
            changed_gold = replace(
                f.gold, alignment_units=(f.gold.alignment_units[0], changed_unit, *f.gold.alignment_units[2:]),
                boundaries=changed_boundaries,
            )
            changed = project_validated_gold(changed_gold, f.chapters, direction)
            self.assertEqual(tuple(item.signature for item in baseline.units), tuple(item.signature for item in changed.units))
            proposal = f.perfect(direction); validated = self._validated(f, proposal)
            self.assertEqual(
                evaluate_validated_proposal(validated, validated_pairwise(baseline, f.root))["exact_units"],
                evaluate_validated_proposal(validated, validated_pairwise(changed, f.root))["exact_units"],
            )

    def test_perfect_grouped_differently_and_partial_exact_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = GoldFixture(Path(directory), complex_units=False)
            direction = AlignmentDirection.ZH_EN
            projection = project_validated_gold(f.gold, f.chapters, direction)
            perfect = f.perfect(direction)
            benchmark = validated_pairwise(projection, f.root)
            perfect_result = evaluate_validated_proposal(self._validated(f, perfect), benchmark)
            self.assertEqual(perfect_result["exact_units"], {
                "matches": 3, "total_proposal_unit_count": 3,
                "scored_candidate_unit_count": 3, "gold_count": 3,
                "precision": 1.0, "recall": 1.0, "f1": 1.0,
            })
            self.assertEqual(perfect_result["coverage"]["left"]["candidate_source_coverage"], 0.75)
            self.assertEqual(perfect_result["coverage"]["right"]["candidate_source_coverage"], 0.75)
            self.assertEqual(perfect_result["dispositions"]["disposition_intrusion_unit_count"], 0)
            grouped_units = (
                ProposalUnit("u000001", ProposalSide(paragraphs=(f.zh.paragraphs[0].id, f.zh.paragraphs[1].id)), ProposalSide(paragraphs=(f.en.paragraphs[0].id, f.en.paragraphs[1].id))),
                ProposalUnit("u000002", paragraph_side(f.zh, 3), paragraph_side(f.en, 3)),
            )
            grouped = replace(perfect, units=grouped_units)
            grouped_metrics = evaluate_validated_proposal(self._validated(f, grouped), benchmark)["exact_units"]
            self.assertEqual((grouped_metrics["matches"], grouped_metrics["precision"], grouped_metrics["recall"]), (1, 0.5, 1 / 3))
            partial = replace(perfect, units=(perfect.units[0],))
            partial_metrics = evaluate_validated_proposal(self._validated(f, partial), benchmark)["exact_units"]
            self.assertEqual((partial_metrics["precision"], partial_metrics["recall"]), (1.0, 1 / 3))

    def test_complete_proposal_isolates_dispositions_outside_exact_scoring(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = GoldFixture(Path(directory), complex_units=False)
            projection = project_validated_gold(f.gold, f.chapters, AlignmentDirection.ZH_EN)
            complete = f.complete(AlignmentDirection.ZH_EN)
            validated = self._validated(f, complete)  # COMPLETE structural validation remains Gold-independent.
            result = evaluate_validated_proposal(validated, validated_pairwise(projection, f.root))
            self.assertEqual(result["exact_units"], {
                "matches": 3, "total_proposal_unit_count": 5,
                "scored_candidate_unit_count": 3, "gold_count": 3,
                "precision": 1.0, "recall": 1.0, "f1": 1.0,
            })
            self.assertEqual(result["coverage"]["left"]["candidate_source_coverage"], 1.0)
            self.assertEqual(result["coverage"]["right"]["candidate_source_coverage"], 1.0)
            self.assertEqual(result["dispositions"]["correctly_isolated_disposition_unit_count"], 2)
            self.assertEqual(result["dispositions"]["disposition_intrusion_unit_count"], 0)
            self.assertEqual(result["dispositions"]["left"]["correctly_isolated_disposition_paragraph_count"], 1)
            self.assertEqual(result["dispositions"]["right"]["correctly_isolated_disposition_paragraph_count"], 1)

    def test_grouped_dispositions_have_no_gold_grouping_truth(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = GoldFixture(Path(directory), complex_units=False, disposition_count=2)
            projection = project_validated_gold(f.gold, f.chapters, AlignmentDirection.ZH_EN)
            result = evaluate_validated_proposal(
                self._validated(f, f.complete(AlignmentDirection.ZH_EN, group_dispositions=True)),
                validated_pairwise(projection, f.root),
            )
            self.assertEqual(result["exact_units"]["total_proposal_unit_count"], 5)
            self.assertEqual(result["exact_units"]["scored_candidate_unit_count"], 3)
            self.assertEqual(result["exact_units"]["f1"], 1.0)
            self.assertEqual(result["dispositions"]["correctly_isolated_disposition_unit_count"], 2)
            self.assertEqual(result["dispositions"]["left"]["correctly_isolated_disposition_paragraph_count"], 2)
            self.assertEqual(result["dispositions"]["right"]["correctly_isolated_disposition_paragraph_count"], 2)
            self.assertEqual(result["dispositions"]["disposition_intrusion_unit_count"], 0)

    def test_disposition_intrusion_shapes_remain_scored_false_positives(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = GoldFixture(Path(directory), complex_units=False)
            direction = AlignmentDirection.ZH_EN
            projection = project_validated_gold(f.gold, f.chapters, direction)
            perfect = f.perfect(direction)
            cases = {
                "disposition-to-story": ProposalUnit("u000003", paragraph_side(f.zh, 4), paragraph_side(f.en, 3)),
                "mixed-with-alignable": ProposalUnit("u000003", paragraph_side(f.zh, 3, 4), paragraph_side(f.en, 3)),
                "dispositions-on-both-sides": ProposalUnit("u000003", paragraph_side(f.zh, 4), paragraph_side(f.en, 4)),
            }
            for name, final_unit in cases.items():
                proposal = replace(perfect, units=(*perfect.units[:2], final_unit))
                result = evaluate_validated_proposal(
                    self._validated(f, proposal), validated_pairwise(projection, f.root),
                )
                with self.subTest(name=name):
                    self.assertEqual(result["exact_units"]["total_proposal_unit_count"], 3)
                    self.assertEqual(result["exact_units"]["scored_candidate_unit_count"], 3)
                    self.assertEqual(result["exact_units"]["matches"], 2)
                    self.assertLess(result["exact_units"]["precision"], 1.0)
                    self.assertLess(result["exact_units"]["f1"], 1.0)
                    self.assertEqual(result["dispositions"]["disposition_intrusion_unit_count"], 1)
                    self.assertEqual(result["dispositions"]["correctly_isolated_disposition_unit_count"], 0)

    def test_empty_zero_rules_coverage_intrusions_and_unmatched_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = GoldFixture(Path(directory))
            projection = project_validated_gold(f.gold, f.chapters, AlignmentDirection.ZH_EN)
            empty = replace(f.perfect(AlignmentDirection.ZH_EN), units=())
            benchmark = validated_pairwise(projection, f.root)
            empty_result = evaluate_validated_proposal(self._validated(f, empty), benchmark)
            self.assertEqual(empty_result["exact_units"], {
                "matches": 0, "total_proposal_unit_count": 0,
                "scored_candidate_unit_count": 0, "gold_count": 4,
                "precision": 0.0, "recall": 0.0, "f1": 0.0,
            })
            intrusion_unit = ProposalUnit(
                "u000001", paragraph_side(f.zh, 4), paragraph_side(f.en, 4),
            )
            intrusion = replace(f.perfect(AlignmentDirection.ZH_EN), units=(intrusion_unit,))
            result = evaluate_validated_proposal(self._validated(f, intrusion), benchmark)
            self.assertEqual(result["exact_units"]["matches"], 0)
            self.assertEqual(result["coverage"]["left"]["candidate_source_coverage"], 0.25)
            self.assertEqual(result["coverage"]["left"]["gold_alignable_coverage"], 0.0)
            self.assertEqual(result["dispositions"]["left"]["disposition_intrusion_ids"], [str(f.zh.paragraphs[3].id)])
            self.assertEqual(result["dispositions"]["disposition_intrusion_unit_count"], 1)
            perfect_result = evaluate_validated_proposal(
                self._validated(f, f.perfect(AlignmentDirection.ZH_EN)), benchmark,
            )
            self.assertEqual(perfect_result["unmatched"], {
                "total_candidate_unmatched_unit_count": 2,
                "scored_candidate_unmatched_unit_count": 2,
                "gold_pairwise_gap_unit_count": 2,
                "exact_unmatched_unit_matches": 2,
            })

    def test_both_empty_zero_rule(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); paths = PathPolicy(root)
            zh, en, ru = make_chapter(Language.ZH, "zh", 0), make_chapter(Language.EN, "en", 0), make_chapter(Language.RU, "ru-official", 1)
            chapters = (zh, en, ru); refs = []
            for chapter in chapters:
                path = paths.normalized_chapter(chapter.source, 1); save_chapter(path, chapter)
                refs.append(GoldSourceRef(chapter.source, chapter.language, paths.relative(path), sha256(path)))
            unit = GoldAlignmentUnit(
                alignment_unit_id(zh.id, 1), GoldAlignmentSide(gap=GoldGap(GoldFlag.EDITION_DIFFERENCE)),
                GoldAlignmentSide(gap=GoldGap(GoldFlag.EDITION_DIFFERENCE)), gold_side(ru, 1),
            )
            gold = GoldChapter("1.0-draft", GoldStatus.DRAFT, zh.id, tuple(refs), (unit,), ())
            projection = project_validated_gold(gold, chapters, AlignmentDirection.ZH_EN)
            params = {}; proposal = PairwiseAlignmentProposal(
                PAIRWISE_ALIGNMENT_PROPOSAL_SCHEMA_VERSION, PAIRWISE_ALIGNMENT_PROPOSAL_ARTIFACT_TYPE,
                "lotm", 1, AlignmentDirection.ZH_EN, CoverageMode.PARTIAL,
                ProposalSourceSnapshot(zh.source, Language.ZH, refs[0].normalized_path, refs[0].normalized_sha256, zh.schema_version, 0),
                ProposalSourceSnapshot(en.source, Language.EN, refs[1].normalized_path, refs[1].normalized_sha256, en.schema_version, 0),
                ProducerProvenance("test", "1", params, parameters_sha256(params)), (),
            )
            result = evaluate_validated_proposal(
                load_and_validate_proposal(proposal, root), validated_pairwise(projection, root),
            )
            self.assertEqual(result["exact_units"], {
                "matches": 0, "total_proposal_unit_count": 0,
                "scored_candidate_unit_count": 0, "gold_count": 0,
                "precision": 1.0, "recall": 1.0, "f1": 1.0,
            })

    def test_candidate_nonempty_gold_empty_zero_rule_and_two_sided_disposition_intrusion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); paths = PathPolicy(root)
            zh, en, ru = make_chapter(Language.ZH, "zh", 1), make_chapter(Language.EN, "en", 1), make_chapter(Language.RU, "ru-official", 0)
            chapters = (zh, en, ru); refs = []
            for chapter in chapters:
                path = paths.normalized_chapter(chapter.source, 1); save_chapter(path, chapter)
                refs.append(GoldSourceRef(chapter.source, chapter.language, paths.relative(path), sha256(path)))
            dispositions = (
                ParagraphDisposition(zh.paragraphs[0].id, ParagraphDispositionReason.METADATA),
                ParagraphDisposition(en.paragraphs[0].id, ParagraphDispositionReason.METADATA),
            )
            gold = GoldChapter("1.0-draft", GoldStatus.DRAFT, zh.id, tuple(refs), (), (), None, dispositions)
            projection = project_validated_gold(gold, chapters, AlignmentDirection.ZH_EN)
            params = {}; proposal = PairwiseAlignmentProposal(
                PAIRWISE_ALIGNMENT_PROPOSAL_SCHEMA_VERSION, PAIRWISE_ALIGNMENT_PROPOSAL_ARTIFACT_TYPE,
                "lotm", 1, AlignmentDirection.ZH_EN, CoverageMode.PARTIAL,
                ProposalSourceSnapshot(zh.source, Language.ZH, refs[0].normalized_path, refs[0].normalized_sha256, zh.schema_version, 1),
                ProposalSourceSnapshot(en.source, Language.EN, refs[1].normalized_path, refs[1].normalized_sha256, en.schema_version, 1),
                ProducerProvenance("test", "1", params, parameters_sha256(params)),
                (ProposalUnit("u000001", paragraph_side(zh, 1), paragraph_side(en, 1)),),
            )
            result = evaluate_validated_proposal(
                load_and_validate_proposal(proposal, root), validated_pairwise(projection, root),
            )
            self.assertEqual(result["exact_units"], {
                "matches": 0, "total_proposal_unit_count": 1,
                "scored_candidate_unit_count": 1, "gold_count": 0,
                "precision": 0.0, "recall": 0.0, "f1": 0.0,
            })
            self.assertEqual(result["dispositions"]["disposition_intrusion_unit_count"], 1)
            self.assertEqual(result["dispositions"]["left"]["disposition_intrusion_paragraph_count"], 1)
            self.assertEqual(result["dispositions"]["right"]["disposition_intrusion_paragraph_count"], 1)

    def test_evaluation_is_deterministic_and_invalid_input_fails_before_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = GoldFixture(Path(directory)); proposal = f.perfect(AlignmentDirection.ZH_RU)
            proposal_path = f.root / "proposal.json"; save_proposal(proposal_path, proposal)
            benchmark = pairwise_benchmark_from_projection(project_validated_gold(
                f.gold, f.chapters, AlignmentDirection.ZH_RU,
            ))
            benchmark_path = f.paths.pairwise_gold_chapter(
                benchmark.left_source.source_id, benchmark.right_source.source_id,
                benchmark.chapter,
            )
            save_pairwise_gold(benchmark_path, benchmark)
            first = evaluate_proposal_files(proposal_path, benchmark_path, f.root)
            second = evaluate_proposal_files(proposal_path, benchmark_path, f.root)
            self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
            broken = replace(proposal, left_source=replace(proposal.left_source, normalized_sha256="b" * 64))
            save_proposal(proposal_path, broken)
            with self.assertRaises(ProposalValidationError): evaluate_proposal_files(proposal_path, benchmark_path, f.root)
            alternate = f.root / "alternate-zh.json"
            alternate.write_bytes(f.chapter_paths[0].read_bytes())
            different_path = replace(
                proposal, left_source=replace(proposal.left_source, normalized_path=f.paths.relative(alternate)),
            )
            save_proposal(proposal_path, different_path)
            with self.assertRaisesRegex(ValueError, "canonical|normalized paths differ"):
                evaluate_proposal_files(proposal_path, benchmark_path, f.root)


class AlignmentCliAndSafetyTests(unittest.TestCase):
    def test_phase3_dependency_direction_is_isolated(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for folder in (root / "src/lotm_v2/gold", root / "src/lotm_v2/review"):
            for path in folder.glob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                modules = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
                modules += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
                self.assertFalse(any("alignment" in module.split(".") for module in modules), path)
        for path in (root / "src/lotm_v2/alignment").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            modules = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
            modules += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
            self.assertFalse(any(set(module.split(".")) & {"review", "hints", "lotm_translator"} for module in modules), path)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "gold.io":
                    self.assertNotIn("save_gold", {alias.name for alias in node.names})
        validation_tree = ast.parse((root / "src/lotm_v2/alignment/validation.py").read_text(encoding="utf-8"))
        validation_modules = [
            node.module or "" for node in ast.walk(validation_tree) if isinstance(node, ast.ImportFrom)
        ]
        self.assertFalse(any("gold" in module.split(".") for module in validation_modules))

    def test_cli_validation_evaluation_and_synthetic_mutation_safety(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f = GoldFixture(Path(directory)); proposal = f.perfect(AlignmentDirection.ZH_EN)
            proposal_path = f.root / "proposal.json"; save_proposal(proposal_path, proposal)
            benchmark = pairwise_benchmark_from_projection(project_validated_gold(
                f.gold, f.chapters, AlignmentDirection.ZH_EN,
            ))
            benchmark_path = f.paths.pairwise_gold_chapter(
                benchmark.left_source.source_id, benchmark.right_source.source_id,
                benchmark.chapter,
            )
            save_pairwise_gold(benchmark_path, benchmark)
            session_path = f.paths.active_review_session("lotm", 1)
            session_path.parent.mkdir(parents=True, exist_ok=True); session_path.write_bytes(b"synthetic-session-sentinel")
            protected = (f.gold_path, *f.chapter_paths, session_path)
            before = tuple(path.read_bytes() for path in protected)
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(cli_main(["alignment-proposal-validate", str(proposal_path), "--root", str(f.root)]), 0)
                validation_output = json.loads(output.getvalue())
            self.assertTrue(validation_output["validation"]["proposal_valid"])
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(cli_main(["alignment-evaluate", str(proposal_path), str(benchmark_path), "--root", str(f.root)]), 0)
                evaluation_output = json.loads(output.getvalue())
            self.assertEqual(evaluation_output["exact_units"]["f1"], 1.0)
            self.assertEqual(tuple(path.read_bytes() for path in protected), before)
            broken = replace(proposal, left_source=replace(proposal.left_source, normalized_sha256="b" * 64))
            save_proposal(proposal_path, broken)
            with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as exit_error:
                cli_main(["alignment-proposal-validate", str(proposal_path), "--root", str(f.root)])
            self.assertNotEqual(exit_error.exception.code, 0)
            self.assertEqual(tuple(path.read_bytes() for path in protected), before)

    def test_real_chapter1_trilingual_projection_is_bootstrap_wiring_only_and_read_only(self) -> None:
        root = Path(__file__).resolve().parents[1]
        gold_path = root / "data/gold/v2/ch_0001.json"
        protected = (
            gold_path, root / "data/normalized/v2/zh/ch_0001.json",
            root / "data/normalized/v2/en/ch_0001.json",
            root / "data/normalized/v2/ru-official/ch_0001.json",
        )
        before = tuple(path.read_bytes() for path in protected)
        gold, chapters = load_fully_validated_gold(gold_path, root)
        for direction in (AlignmentDirection.ZH_EN, AlignmentDirection.ZH_RU):
            projection = project_validated_gold(gold, chapters, direction)
            left = next(item for item in chapters if item.language is Language.ZH)
            right = next(item for item in chapters if item.language is direction.right_language)
            left_ref = next(item for item in gold.sources if item.language is Language.ZH)
            right_ref = next(item for item in gold.sources if item.language is direction.right_language)
            params = {"purpose": "evaluator-self-consistency-smoke"}
            units = tuple(
                ProposalUnit(
                    proposal_unit_id(index),
                    unmatched() if item.signature[0] is None else ProposalSide(paragraphs=tuple(ParagraphId.parse(value) for value in item.signature[0])),
                    unmatched() if item.signature[1] is None else ProposalSide(paragraphs=tuple(ParagraphId.parse(value) for value in item.signature[1])),
                )
                for index, item in enumerate(projection.units, start=1)
            )
            proposal = PairwiseAlignmentProposal(
                PAIRWISE_ALIGNMENT_PROPOSAL_SCHEMA_VERSION, PAIRWISE_ALIGNMENT_PROPOSAL_ARTIFACT_TYPE,
                gold.chapter.work_id, gold.chapter.number, direction, CoverageMode.PARTIAL,
                ProposalSourceSnapshot(left.source, left.language, left_ref.normalized_path, left_ref.normalized_sha256, left.schema_version, len(left.paragraphs)),
                ProposalSourceSnapshot(right.source, right.language, right_ref.normalized_path, right_ref.normalized_sha256, right.schema_version, len(right.paragraphs)),
                ProducerProvenance("test-only-gold-projection", "1", params, parameters_sha256(params)), units,
            )
            validated = load_and_validate_proposal(proposal, root)
            self.assertEqual(len(validated.proposal.units), len(projection.units))
            if direction is AlignmentDirection.ZH_EN:
                self.assertEqual(len(projection.units), 46)
                self.assertEqual(sum(len(item.signature[1] or ()) for item in projection.units), 68)
                self.assertEqual(projection.right_disposition_ids, (
                    "lotm:en:0001:p000069", "lotm:en:0001:p000070", "lotm:en:0001:p000071",
                ))
        self.assertEqual(tuple(path.read_bytes() for path in protected), before)


if __name__ == "__main__":
    unittest.main()
