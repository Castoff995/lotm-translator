# Phase 3 Pairwise Alignment Contract

Task 005 establishes the Phase 3 proposal/evaluation contract. No production
alignment generator exists yet, and Phase 3 is not complete.

The permitted data flow is strictly read-only with respect to corpus truth:

```text
normalized physical Paragraphs
-> one machine pairwise-alignment proposal
-> structural validation
-> read-only evaluation against fully validated independent human Pairwise Gold
```

A proposal is an ordered hypothesis from one algorithm run. It is neither a
human Gold `AlignmentUnit` nor a candidate lattice, n-best pool, ensemble, or
reranker input. Proposal-unit IDs (`u000001`, ...) are sequential run-local
identifiers; stable identity remains the referenced physical `ParagraphId`.

## Canonical directions and units

The only production directions in this contract are `zh-en` and `zh-ru`:
left is always ZH and right is EN or RU. EN-RU remains a future consistency
signal. Direction is validated from typed source and language identity, never
inferred from a path or filename.

Each side of a proposal unit contains exactly one of:

```json
{"paragraphs": ["complete stable Paragraph IDs"]}
{"unmatched": true}
```

Both sides cannot be unmatched. A unit can therefore represent 1-1, 1-N,
N-1, N-M, or source-only structure. A proposal never splits a physical
Paragraph, introduces sentence/offset identity, or copies source text.

Machine `unmatched` is structural evidence only. It carries no human reason
and is not Gold `GAP`. Evaluation may find an exact structural match against a
Gold unit with one GAP, while the human GAP reason remains outside the machine
signature.

## Artifact identity and coverage

`pairwise_alignment_proposal` schema `1.0-draft` freezes work/chapter,
direction, explicit coverage mode, source snapshots, producer provenance and
one ordered proposal path. Each source snapshot freezes source ID, language,
normalized path, byte SHA-256, normalized schema version and physical
Paragraph count. Loading fails closed on any mismatch.

Producer provenance records ID/version, deterministic canonical-JSON
parameters SHA-256, optional code revision, and parameters only. Artifacts do
not store secrets, environment dumps, source text, model weights, or binary
data. Optional finite algorithm-native scores are evidence only; their declared
semantics do not imply a `[0, 1]` range or authorize acceptance.

Coverage mode is explicit:

- `partial` permits skipped source Paragraphs and reports them;
- `complete` requires every physical Paragraph of both selected sources exactly
  once on a paragraph-backed side. Source-only content uses `unmatched` on the
  opposite side.

Partial skips are not converted to dispositions or unmatched units. Duplicate,
unknown, wrong-source, wrong-chapter, non-monotonic and crossing references are
invalid in both modes. Every paragraph-backed unit side must be a contiguous
physical range. A partial proposal may skip Paragraphs only between units.
Loaded normalized Chapter ordering is authoritative.

## Independent Pairwise Gold benchmark

Evaluation accepts only `pairwise_gold` as described in
[`pairwise-gold.md`](pairwise-gold.md). It freezes the exact two source IDs,
normalized revisions and full human-reviewed coverage. Trilingual Gold is not
an authoritative pairwise benchmark and is rejected by `alignment-evaluate`.

One Pairwise Gold GAP becomes proposal-side unmatched for structural signature
comparison; the human GAP reason remains outside the exact signature.

`ParagraphDisposition` entries never become projected Gold pairwise units and
define no AlignmentUnit grouping truth. A COMPLETE machine proposal may still
cover disposition Paragraphs in disposition-only structural units: every
paragraph-backed item must be a disposition on exactly one side and the other
side must be `unmatched`. Such units remain in total proposal count and raw
coverage, but are classified separately and excluded from exact scoring.

Any other proposal unit containing a disposition Paragraph is an intrusion and
remains a scored non-matching candidate/false positive. This includes pairing a
disposition with paragraph-backed content, mixing it with alignable Paragraphs,
or placing disposition content on both sides. Gold JOIN/BREAK, flags, notes and
GAP reasons do not enter structural signatures or Phase 3 scores; changing only
those annotations cannot change metrics.

## Evaluation protocol v1

Evaluator version `1.0` compares complete signatures:

```text
(ordered left Paragraph-ID tuple OR UNMATCHED,
 ordered right Paragraph-ID tuple OR UNMATCHED)
```

There is no partial credit for overlap, semantic similarity, or different
grouping. Matching uses multiset-safe counting. The precision denominator is
`scored_candidate_unit_count`, not `total_proposal_unit_count`; correctly
isolated disposition units are the difference. Exact-unit precision, recall
and F1 use these frozen zero-denominator rules:

```text
scored candidate=0, gold>0  -> precision=0, recall=0, f1=0
scored candidate>0, gold=0  -> precision=0, recall=0, f1=0
scored candidate=0, gold=0  -> precision=1, recall=1, f1=1
```

Each side also reports raw source coverage, skipped Paragraph count, coverage
of Gold-alignable Paragraphs, missing Gold-alignable Paragraphs, isolated
disposition IDs and disposition-intrusion IDs. Counts distinguish total proposal
units, scored candidate units, isolated disposition units and intrusion units.
A zero-size raw/Gold-alignable set has coverage `1.0` when zero items are
covered. Unmatched diagnostics distinguish total and scored candidate unmatched
units; exact unmatched matches use only scored candidates, so isolated
dispositions cannot inflate Gold GAP matching.

Evaluation reads proposal and Pairwise Gold bytes once. Each parsed object and
reported file SHA-256 derive from that same immutable in-memory snapshot, so a
path replacement after the read cannot mix metrics from one revision with
provenance from another. Normalized chapters follow the same read/hash/parse
snapshot principle during structural validation.

Evaluation JSON identifies canonical document and exact file hashes for the
proposal and Pairwise Gold, producer/config/code revision, evaluator version,
exact source IDs, normalized hashes/schema versions/counts and coverage mode.
Confirmed Pairwise Gold is required by default. `--allow-draft-gold` enables a
visibly labelled `development_draft` result. Invalid input fails before any
score object is returned. The evaluator loads no third source and never mutates
normalized data, Gold, Review Sessions, or source identities.

The dependency-light read-only CLI surface is:

```powershell
.\.venv\Scripts\python.exe -m src.lotm_v2.cli alignment-proposal-validate proposal.json --root PATH
.\.venv\Scripts\python.exe -m src.lotm_v2.cli alignment-evaluate proposal.json pairwise-gold.json --root PATH
```

Local proposal evidence may live under
`data/alignment/v2/proposals/<producer_id>/<direction>/ch_NNNN.json`; it never
belongs under Gold, Review Sessions, or source manifests. No CLI command
exports Gold as a persisted proposal.

The proposal JSON file itself may be external read-only input. Its stored
normalized source references, and those in Pairwise Gold, must be canonical
repository-relative references resolving inside the repository to the exact
source/chapter location. Pairwise Gold used for evaluation must use the exact
centralized source-pair target path; aliases and alternate copies are rejected.

## Extension boundary

The `alignment` package may read normalized Chapters and the Pairwise Gold read
API for evaluation. Pairwise Gold never depends on machine proposal/generator
code. Trilingual bootstrap is a separate read-only assistive path and cannot
affect metrics. Task 005/005.3 adds no
generator, dynamic programming, BGE/SONAR/Bertalign, reranker, ensemble,
trilingual reconciliation, Review UI suggestion, automatic Gold decision,
JOIN/BREAK prediction, TrainingBlock, or dataset export.

Future candidate pools, ensembles, rerankers and model-assisted review require
separate versioned artifacts and architecture review. They must not reinterpret
this one-path proposal artifact or turn evaluation evidence into Gold truth.
