# Independent Pairwise Gold v1 Draft

Pairwise Gold is the authoritative human truth for Phase 3 pairwise evaluation.
It is separate from both machine proposals and the existing trilingual Gold:

```text
machine Pairwise Alignment Proposal -> evaluated against -> human Pairwise Gold
trilingual Gold -> read-only bootstrap context and later reconciliation truth
```

The supported directions are `zh-en` and `zh-ru`. ZH is canonical left; EN or
official RU is right. Both exact `source_id` values are frozen in the artifact,
so language identity never substitutes for edition/source identity.

## Persistent contract

Schema `1.0-draft`, artifact type `pairwise_gold`, stores:

- work, canonical chapter and direction;
- exact left/right normalized source path, byte SHA-256, schema and Paragraph
  count;
- ordered human Pairwise AlignmentUnits;
- human Pairwise ParagraphDispositions;
- non-authoritative creation/bootstrap provenance;
- `draft` or `confirmed` status.

A unit side is exactly one of a non-empty, ordered, contiguous physical
Paragraph range or a human GAP. Both sides cannot be GAP. Unit IDs encode work,
chapter, both exact source IDs and sequence. Pairwise Gold has no `JOIN`,
`BREAK`, TrainingBlock or copied source text.

Every selected physical Paragraph has exactly one fate: membership in one
Pairwise AlignmentUnit XOR one Pairwise ParagraphDisposition. GAP means an
AlignmentUnit exists with no corresponding physical Paragraph on one side.
Disposition means an existing physical Paragraph was reviewed and excluded
from translation correspondence. Neither replaces the other.

## Status and authority

An empty draft contains no inferred decisions. A Review Session may be partial,
but publication into tracked Pairwise Gold requires full exact-once coverage and
preserves `status=draft`. Publication is not benchmark confirmation.

Confirmation is a separate full-validation operation guarded by both the
expected exact file SHA-256 and canonical document SHA-256, plus the absence of
an active Pairwise Review Session. It acquires the same target-Gold OS lock as
Reviewer publication, reads and validates those exact bytes, changes only
`status` to `confirmed`, and atomically replaces and verifies the target while
the lock remains held. Confirmed Pairwise Gold is read-only and is required for
authoritative evaluation. Explicit development evaluation of a draft is
labelled `development_draft`.

Canonical document identity is UTF-8 JSON with sorted keys, compact deterministic
separators, stable Unicode and rejection of NaN/infinity. File SHA-256 separately
identifies the exact bytes loaded.

Tracked location:

```text
data/gold/v2/pairwise/<left_source_id>--<right_source_id>/ch_NNNN.json
```

This is the only accepted human Pairwise Gold location. The exact source IDs in
the document determine the directory; a shortened language alias is invalid.
`pairwise-gold-draft`, Pairwise Review, evaluation, and
`pairwise-gold-confirm` fail closed for a different target. Stored normalized
source references are canonical POSIX-style repository-relative paths. They
must resolve to the exact normalized chapter for the frozen source/chapter and
must not be absolute, drive-relative, UNC, mixed-separator, `..`, or a resolved
symlink/junction escape.

Machine proposals remain ignored evidence under `data/alignment/`; Pairwise
Review Sessions remain ignored WIP under `data/review_sessions/v2/pairwise/`.

## Trilingual bootstrap boundary

A fully validated trilingual Gold document may expose coarse regions, possible
GAPs, out-of-pair regions, disposition suggestions, and third-language context.
Every item carries trilingual unit/disposition identity, trilingual Gold hash
and selected exact source IDs. It is labelled
`TRILINGUAL BOOTSTRAP — NOT PAIRWISE TRUTH`.

Bootstrap never writes Pairwise Gold or a machine proposal, never advances a
cursor, and never confirms a unit/GAP/disposition. Human reviewers may split a
coarse region or merge across regions. Only their explicit Pairwise Review
actions become session decisions.
