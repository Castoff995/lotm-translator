# Human Gold Review Tool

The reusable Phase 2 review tool captures human alignment truth for any Gold
Draft compatible with the current Architecture v2 Gold Schema. It contains no
corpus/Paragraph aligner, AlignmentUnit recommendation, confidence ranking, or
automatic acceptance. An optional isolated hints subsystem may provide clearly
labelled local machine translation and paragraph-internal lexical visualization;
those hints have no Gold write access and are never corpus truth.

## Requirements and launch

Use the project's existing Python virtual environment. From the project root:

```powershell
.\.venv\Scripts\python.exe -m src.lotm_v2.review data\gold\v2\ch_0001.json
```

For a project stored elsewhere, pass `--root PATH`. Use `--port 0` to choose a
free local port or `--no-browser` to print the URL without opening it. The
server binds to `127.0.0.1` by default and stops with Ctrl+C.

The Gold Draft supplies normalized source paths and SHA-256 hashes. On opening,
the tool loads those sources, checks identity and hashes, validates the existing
partial prefix, and reconstructs all cursors. A changed source blocks review
instead of mixing corpus revisions.

## Alignment workflow

The main screen builds one column per canonical ZH, EN and RU source from the
loaded data. It shows paragraph ID, type and normalized text; expand `raw /
provenance` to inspect raw text, exact source lines and paragraphization data.
By default, the previous paragraph and four positions beginning at the current
cursor are displayed. `Контекст вперёд` adjusts that display range independently
for ZH, EN and RU without moving a cursor or changing an alignment selection.
The displayed range always expands to include every paragraph selected by the
current alignment count, even when the saved display-context value is smaller.
Display-context preferences are browser-local UI state scoped by work, chapter
and language; they are not Review Session or Gold data.

For every language choose how many consecutive paragraphs belong to the next
unit. There is no hard group-size limit. Select `GAP` instead when that source
has no corresponding paragraph, then choose a structured reason and optionally
write a note. Press Preview, inspect the complete three-sided group, and press
Confirm. Confirmation changes only the active Review Session working copy; it
does not publish the decision to tracked Gold.

Alternatively, use `Record ParagraphDisposition` for the current paragraph of
one source when a human has reviewed it and decided that it must not participate
in translation alignment. Choose an explicit reason; `other` also requires a
note. This advances only that source cursor. The tool never proposes or creates
a disposition from ParagraphType.

Do not use disposition for meaningful source-only content that still belongs to
translation correspondence. Such content forms an AlignmentUnit with GAPs on
the absent sides. Disposition is reserved for an existing paragraph intentionally
excluded from alignment, such as copyright/legal metadata.

Keyboard shortcuts are shown in the UI: Tab/Shift+Tab changes the active source,
1–9 changes its selection size, Enter previews/confirms, Esc cancels, and Ctrl+Z
undoes the last review action. Font size and scroll position are stored locally in the
browser; neither is corpus truth.

## Local Chinese hints and glossary

The optional Phase 2 hints subsystem is opened explicitly with a local `ZH hints`
action on a visible Chinese paragraph. Nothing is loaded at Review Tool startup.
The Hint Target is an independent UI-only ZH Paragraph selection that persists
across panel open/close and survives review state changes:

```text
review cursor != alignment selection count != display context != Hint Target
```

A reviewer may move this target with:

- `Previous` / `Next` (among physical ZH paragraphs),
- `Go to index` (1..N),
- `Jump to current cursor` (selects the Paragraph at `state.sources.zh.cursor`),
- `Close hints` / `Open ZH hints` (state is preserved between toggles).

For the target paragraph, hints execute this local-only pipeline:

```text
normalized ZH text (read-only input)
-> Ollama ZH-to-EN machine hint
-> Jieba Chinese word segmentation
-> SimAlign paragraph-internal lexical correspondences
-> temporary hover/selection data
```

Install the optional packages with `pip install -r requirements-hints.txt` and
make the configured Ollama model available locally. Missing packages, model
files, or Ollama display `Translation hints unavailable`; Gold review remains
usable. Translation, segmentation, and lexical results are cached separately
under `data/cache/v2/hints`. Cache keys include provider/model/prompt version
and text hashes, so model or source-text changes cannot reuse stale hints.

The panel is always labelled `LOCAL MACHINE TRANSLATION HINT`. Hovering a ZH
token temporarily highlights its soft EN span; reverse EN hover is also
supported. Click selects a token, Shift-click or the range buttons selects a
contiguous multi-token term, and the EN phrase remains human-editable. Scores
are visualization aids only. They do not enter Gold, source provenance,
normalized corpus, paragraph correspondence, cursors, or Phase 3 evidence.

`Add to LOTM glossary` opens a preview containing ZH, editable EN, pending RU,
chapter and stable Paragraph ID. Only explicit confirmation writes the separate
versioned artifact at `data/glossary/v2/<work_id>.json`. The `1.0-draft`
glossary entry stores a stable term ID, canonical ZH/EN, nullable canonical RU,
status (`needs_ru`, `complete`, or `needs_review`), language-specific aliases,
exact ZH Paragraph character span, notes, and created/updated provenance.
Duplicate ZH canonical forms or aliases are shown rather than overwritten.

Glossary preview uses the *current Hint Target paragraph ID and selected
paragraph-local offsets* from `normalized_text`; the same offsets are written
when terms are added. This keeps terminology evidence tied to a physical paragraph,
rather than the cursor state.

The hint workflow is intentionally read-only for Review data:
alignment units, dispositions, boundaries, review session cursor progress, and Gold
state do not change when navigating hints, opening/closing the panel, or making
hint and glossary-preview requests.

The `LOTM Glossary` panel supports search, status filtering, counts, and source
references. Known-term highlighting is opt-in. A new entry starts with
`ru_term: null` and `status: needs_ru`; no generated RU phrase becomes canonical
without a later explicit human workflow. `RussianTermSuggestionProvider` is
only an extension point and has no implementation in this phase.

The Review HTTP layer passes only the already displayed Paragraph ID/text to
the hints service. The hints package does not import or call Gold services and
has no routes that can create AlignmentUnit, GAP, ParagraphDisposition,
JOIN/BREAK, or move cursors. Glossary confirmation is a terminology decision,
not a Gold alignment decision.

## Session lifecycle, save and resume

Review uses this explicit lifecycle:

```text
tracked Gold baseline
-> ignored persistent Review Session
-> atomic autosave after each human action
-> deterministic resume and full validation
-> explicit publication to tracked Gold
```

The familiar launch command resolves a deterministic active session path under
`data/review_sessions/v2/<work_id>/ch_NNNN.active.json`. At most one writable
session exists for a target chapter. Reopening the same Gold automatically
resumes that session with the same session ID, working Gold, cursors, units,
dispositions and boundaries; the user never passes a session path.

Every confirmed unit, disposition, boundary, undo, or rollback atomically
autosaves only the ignored session document. The target Gold file remains
byte-identical until `Publish to Gold`. Session JSON stores stable paragraph
references and human decisions, not source text, glossary data, machine hints,
hover state or lexical scores.

`Undo last action` removes the most recent AlignmentUnit or disposition and any
boundary that becomes invalid. `Rollback to AlignmentUnit N` explicitly keeps
the review history before and through N, deletes downstream units, dispositions
and boundaries, and lets the reviewer rebuild them. This
is safer than silently rewriting later stable unit references. `Discard
session` archives only the ignored working session and never rewrites Gold.
Confirmed Gold opens read-only and does not implicitly create a writable
session.

Active sessions are discoverable for source-migration guardrails:

```powershell
.\.venv\Scripts\python.exe -m src.lotm_v2.cli review-session-list
.\.venv\Scripts\python.exe -m src.lotm_v2.cli review-session-status data\gold\v2\ch_0001.json
```

## Compatibility contracts

Reviewer app version, session JSON schema, review semantics, and Gold schema are
independent compatibility dimensions. An app-version difference alone is
allowed when the other contracts remain supported. Opening a compatible
session updates only `last_opened_with_reviewer`; it never silently changes the
session schema or semantics. Unsupported schema/semantics fail closed. A schema
migration must be explicitly registered, preserve a backup, and preserve the
meaning of human decisions.

| Change | App version | Session schema | Review semantics | Old session reusable? |
|---|---|---|---|---|
| Theme/CSS | may change | same | same | YES |
| Button wording | may change | same | same | YES |
| Non-semantic refactor | may change | same | same | YES |
| Session JSON shape | change as needed | bump | usually same | only with declared compatible migration |
| Cursor meaning | change | maybe same | BUMP | NO without explicit compatibility/migration |
| Paragraph/source changes | unrelated | unrelated | unrelated | NO if source hash changes |
| Gold schema change | change | maybe | maybe | only if Gold schema compatibility exists |

Each session freezes target Gold baseline SHA-256 plus normalized source IDs,
paths, schema versions, paragraph counts and hashes. A normalized-source
mismatch blocks writable resume. An external Gold change blocks publication;
human decisions are never remapped automatically to a new source revision.

## Boundary review

Alignment correspondence and semantic grouping remain separate. For each pair
of adjacent confirmed alignment units, Boundary Review records only:

- `JOIN`: both units should later share one semantic TrainingBlock;
- `BREAK`: a semantic TrainingBlock boundary belongs between them.

The tool does not build TrainingBlocks.

## Validation and publication

Partial validation runs at open and before every save. It permits unfinished
text but requires valid references, sequential unit IDs, exactly one partial
fate per consumed paragraph, no duplicate or skipped paragraphs, contiguous
monotonic ranges, valid explicit GAPs/dispositions, prefix-consistent cursors,
and valid existing boundaries.

`Full Gold validation` rechecks compatibility and source hashes and then invokes
the authoritative full validator. It requires every physical paragraph to have
exactly one fate (AlignmentUnit XOR ParagraphDisposition) and a JOIN or BREAK
after every internal unit. Success only reports that the session is ready.

`Publish to Gold` first shows target, chapter, AlignmentUnit,
ParagraphDisposition and boundary counts, source-verification state, and full
validation result. It requires a separate explicit confirmation. Publication
then rechecks compatibility, sources, partial/full invariants and the original
Gold baseline hash, records a publication intent, atomically replaces Gold,
verifies the resulting hash, and archives the session as `published`. If a
crash occurs after the Gold write but before finalization, a later launch
recognizes the matching expected hash and safely finishes the archive step
without rewriting Gold. Publication does not automatically change Gold status
from `draft` to `confirmed`.
