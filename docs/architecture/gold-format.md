# Gold Format v1 Draft

Gold files use pretty-printed UTF-8 JSON. JSON was selected because it requires
no dependency, is deterministic and diff-friendly, and has unambiguous schema
types. YAML comments are attractive for editing, but parser differences and
implicit scalar conversion would weaken reproducibility.

This document describes the existing trilingual Gold format. Independent
Phase 3 ZH-EN and ZH-RU benchmark truth uses the separate Pairwise Gold contract
in [`pairwise-gold.md`](pairwise-gold.md). Trilingual Gold may assist Pairwise
Review as read-only bootstrap context, but it is not automatically copied or
scored as authoritative pairwise truth.

Legacy trilingual Gold `1.0-draft` preserves its established acceptance
semantics: Paragraph references inside one language side must be unique and
monotonic, but they are not subject to the newer Pairwise within-unit physical
contiguity invariant. Independent Pairwise Gold and Pairwise Alignment Proposal
artifacts do require each paragraph-backed side to be one contiguous physical
Paragraph range. Pairwise validation rules must not be applied retroactively to
reinterpret existing trilingual human Gold under the unchanged schema version.

A gold chapter contains version/status, normalized source references with
checksums, alignment units that reference stable paragraph IDs, explicit gaps,
and independent semantic boundaries. Paragraph text remains canonical in the
normalized chapter JSON; it is not copied into each alignment unit.

Generated pilot files use `status: draft`. A draft is never gold truth merely
because a tool generated it. Human reviewers add/correct alignment units and a
`JOIN` or `BREAK` boundary after every internal unit, run validation, and only
then may change status to `confirmed`.

Full validation requires exactly one human-reviewed Gold fate for every
physical paragraph from every referenced normalized source. A paragraph is
either referenced exactly once by an alignment unit or exactly once by a
`ParagraphDisposition`; it cannot be both. This includes headings, separators,
notes, footnotes and metadata, none of which may disappear silently.

`ParagraphDisposition` records that an existing physical paragraph was reviewed
and intentionally excluded from translation alignment. It references only the
stable Paragraph ID and has one of these reasons: `heading`, `metadata`,
`separator`, `footnote`, `publisher_note`, `translator_note`,
`non_story_content`, or `other`. `other` requires a note. The choice is always a
human Gold decision; `ParagraphType` never creates a disposition automatically.

Disposition history stores `after_alignment_unit`, the number of alignment
units that existed when the decision was recorded. This non-semantic review
anchor makes mixed alignment/disposition cursors, undo, rollback and resume
deterministic without copying paragraph text or turning dispositions into
alignment decisions.

Explicit gaps have a structured reason (`addition`, `omission`,
`translator_note`, or `edition_difference`) and an optional note. A fake empty
paragraph is prohibited.

GAP and ParagraphDisposition are different. GAP means an alignment unit exists
but one source has no corresponding paragraph. ParagraphDisposition means an
existing paragraph does not participate in translation alignment. Meaningful
edition-specific content therefore belongs in an AlignmentUnit with GAPs on the
absent sides; copyright/legal metadata may instead receive a metadata
disposition. A fake AlignmentUnit must not be created only to satisfy coverage.
