# AGENTS.md — LOTM Translator

These instructions apply to all coding agents working in this repository.

The normative architecture for new development is **Architecture v2**.
`src/lotm_translator/` is legacy v1 and must not receive new v2 domain behavior.

## 1. Before touching files

Always inspect the current repository state first:

```text
git branch --show-current
git status
```

Read the relevant architecture documentation before implementation, at minimum:

```text
docs/architecture/v2.md
docs/architecture/guardrails.md
```

For review/Gold work also read:

```text
docs/gold-review-tool.md
```

Do not assume that a clean Git working tree means there is no human work in progress.
Review Sessions are intentionally ignored by Git.

Before changing any of the following:

```text
normalized source bytes
paragraphization
stable Paragraph IDs
source identity
canonical chapter mapping
source revision
```

run:

```text
python -m src.lotm_v2.cli review-session-list
```

using the project virtual environment where applicable.

If an active Review Session is relevant to the source/chapter being changed, STOP.
Do not silently migrate, rewrite, discard, or reinterpret human WIP.

## 2. Architecture Guardrail — mandatory STOP rule

Do not implement a request automatically if it conflicts with Architecture v2.

STOP before editing if a request would:

- mix `Paragraph`, `AlignmentUnit`, and `TrainingBlock`;
- let paragraph alignment silently define semantic grouping;
- move v2 concepts into deprecated v1 modules;
- mutate immutable raw data;
- lose or weaken provenance;
- infer source authority from filenames/directories instead of manifests;
- silently delete or rewrite source-like content during normalization;
- change stable Paragraph identity without an explicit source revision/migration decision;
- reassign canonical chapter identity in a frozen Source Chapter Map;
- silently reinterpret existing human Gold;
- silently reinterpret an active Review Session;
- reopen confirmed Gold implicitly;
- use hints/model output as Gold truth;
- implement functionality belonging to a later architecture phase;
- add a workaround that changes an approved architectural decision;
- introduce major technical debt just to make the current task easier.

When stopping, report:

1. the exact conflict;
2. the architecture principle being violated;
3. the technical consequence;
4. the smallest compatible alternative;
5. the model/escalation level recommended if architecture review is needed.

Then wait for explicit user approval.

## 3. Core entity boundaries

### Paragraph

A `Paragraph` is one physical/editorial source paragraph.

It is not:

- a sentence;
- an alignment candidate;
- an AlignmentUnit;
- a TrainingBlock.

Its stable identity must not depend on an alignment algorithm or model.

### AlignmentUnit

An `AlignmentUnit` answers only:

```text
which source Paragraphs correspond?
```

It may be many-to-many and may contain an explicit GAP.

It does not define future model context boundaries.

### TrainingBlock

A `TrainingBlock` is a later semantic grouping of confirmed AlignmentUnits.

Do not implement production TrainingBlock behavior before its architecture phase.

### JOIN / BREAK

Gold `JOIN` / `BREAK` labels are independent human annotations for future semantic grouping.

They must not be inferred automatically from AlignmentUnit structure.

## 4. Source authority

Use this source hierarchy:

```text
1. ZH original — primary meaning authority
2. official EN — semantic auxiliary
3. official RU — primary RU target/style reference
4. fan RU — secondary reference only
```

Source role must come from versioned source metadata/manifests, never from a path or filename guess.

Canonical `ChapterId.number` is the global corpus chapter number.

Source-local numbers, TOC indices, numbering scopes, and volume resets are not canonical chapter identity by themselves.

Frozen Source Chapter Maps must fail closed on identity conflict.

## 5. Raw / normalization / provenance

Raw source data is immutable authority.

Allowed flow:

```text
immutable raw source
-> versioned normalization
-> downstream representations
```

Normalization must be conservative and reproducible.

Do not silently remove:

- headings;
- footnotes;
- translator notes;
- cultural explanations;
- separators;
- metadata;
- story-like content.

Preserve provenance sufficient to recover the physical source location.

Existing stable Paragraph IDs must never silently begin referring to different text.

## 6. Gold and Review Sessions

Published Gold and human work-in-progress are different persistence layers.

Expected flow:

```text
Gold baseline
-> ignored Review Session
-> autosave / resume
-> full validation
-> explicit publication
-> Gold
```

Ordinary review actions such as:

```text
Confirm
GAP
ParagraphDisposition
JOIN
BREAK
Undo
Rollback
```

must mutate the active Review Session only.

They must not write tracked Gold until explicit publication.

Do not:

- auto-publish;
- auto-confirm;
- silently reopen confirmed Gold;
- migrate human decisions to changed source Paragraphs;
- mix hints/glossary data into Gold truth;
- discard a real active human Review Session during automated tests.

Real Chapter 1 or other human corpus data must not be mutated merely to smoke-test code.

Prefer synthetic fixtures for automated tests.

## 7. Hints subsystem boundary

The `hints` subsystem is an isolated Phase 2 assistive tool.

Allowed:

```text
one displayed physical ZH Paragraph
-> local ZH->EN machine hint
-> paragraph-internal segmentation
-> paragraph-internal lexical visualization
-> glossary assistance
```

Not allowed:

- corpus paragraph alignment;
- AlignmentUnit suggestion/ranking;
- candidate scoring for Gold;
- confidence-as-truth;
- cursor mutation;
- Gold mutation;
- automatic GAP/Disposition/JOIN/BREAK decisions;
- semantic grouping;
- source truth mutation.

Hints are machine assistance only.

Hint Target, Review cursor, and Alignment selection are independent UI states.

A Hint Target must remain one physical Paragraph at a time so paragraph-local offsets and glossary provenance stay unambiguous.

## 8. Current phase boundaries

Do not prematurely implement later-phase systems unless the task explicitly changes the architecture plan.

In particular, do not introduce production versions of:

```text
BGE / SONAR paragraph alignment
Bertalign ensemble
candidate reranking
dynamic-programming alignment
confidence ranking
Qwen paragraph alignment judgment
trilingual reconciliation
semantic TrainingBlock grouping
training JSONL export
LoRA training pipeline
```

merely because they could simplify a current Phase 0–2 task.

A narrow local lexical aligner inside `hints` does not authorize corpus-level alignment.

## 9. Minimal-diff rule

Prefer the smallest correct change.

Do not rewrite, reformat, translate, rename, or reorganize unrelated code while implementing a focused task.

In particular:

- preserve existing UI language unless the task asks to change it;
- preserve established formatting style;
- do not convert a compact frontend file into a large rewrite merely to add a small feature;
- do not replace working review logic when a local extension is sufficient;
- do not perform opportunistic refactors unrelated to acceptance criteria.

If a focused task appears to require a large rewrite, STOP and explain why before doing it.

Large diffs require explicit justification.

## 10. Regression-first behavior

Before modifying existing behavior:

1. identify the known-good behavior;
2. preserve it unless the task explicitly changes it;
3. add tests for the requested behavior;
4. test old behavior again.

For frontend review changes, explicitly protect:

```text
alignment counts
preview
confirm
dialog close
cursor advance
Undo
Rollback
GAP
ParagraphDisposition
JOIN/BREAK
Publish
Discard
keyboard shortcuts
Review Session autosave
```

A new feature must not silently replace these workflows.

## 11. Model selection rule

Tasks may specify a required or recommended Codex model.

A line inside a task such as:

```text
Required model: GPT-5.6 Sol Medium
```

does **not** switch the active model.

If a task declares a required model:

1. verify the active model if the client exposes it;
2. if it does not expose the model, require explicit user confirmation;
3. if the active model does not match, STOP before changing files.

Do not continue merely because the model name appears in the task text.

General guidance:

```text
Spark
- narrow local change
- small UI/CSS/test/CLI edits
- easy-to-verify implementation

Sol Medium
- normal multi-file implementation
- debugging
- integration work
- regression repair

Sol High
- architecture
- schema/identity changes
- Gold semantics
- migrations
- source identity
- high-risk core decisions
```

Escalate rather than forcing a weaker model through an architecture-sensitive task.

## 12. Shell correctness

Use syntax appropriate to the shell actually being used.

Do not mix:

```text
PowerShell line continuation: `
Git Bash line continuation: \
```

Do not assume commands written for one shell are valid in another.

When reporting commands to the user, clearly label whether they are for:

```text
PowerShell
Git Bash
```

## 13. Tests and validation

Use the repository's project virtual environment.

The normal Python suite is:

```text
python -m pytest tests
```

Do not treat unrelated experimental scripts such as `scripts/test_bertalign.py` as the main test suite.

Do not install unrelated heavy dependencies merely because a broad test-discovery command picked up an experimental script.

For a normal code task, run as applicable:

```text
python -m pytest tests
git diff --check
git status
git diff --stat
```

Run targeted tests first when debugging, then the full `tests` suite before completion.

If Node is unavailable, do not silently install it solely for a syntax check unless the task requires it. Use available validation and report the limitation.

Warnings must be distinguished from failures.

## 14. Real-data smoke tests

Automated tests should use synthetic data whenever possible.

If a real corpus smoke test is useful:

- do not publish Gold;
- do not create lasting human decisions automatically;
- do not overwrite source data;
- do not discard an existing human session;
- record/check hashes when the task concerns mutation safety;
- leave manual semantic decisions to the user.

If a smoke test creates a temporary empty Review Session, clean it up only if it is known to be test-created and contains no human work.

## 15. Git safety

Unless the user explicitly asks otherwise:

```text
NO COMMIT
NO PUSH
```

for implementation tasks.

Leave changes in the working tree for review.

Before final reporting:

```text
git status
git diff --stat
git diff --check
```

Do not stage unrelated files.

Do not use destructive Git operations on user work.

Never use broad commands such as these to "clean things up" without explicit approval:

```text
git reset --hard
git clean -fd
git checkout -- .
git restore .
```

When restoring a known-good file for a regression repair, limit the restore to explicitly named files and state exactly what will be discarded.

## 16. Completion report

At the end of an implementation task report:

- model used / confirmation basis;
- branch and relevant HEAD;
- root cause if debugging;
- exact changed files;
- architecture/schema/version changes;
- tests run and results;
- warnings/limitations;
- `git diff --check`;
- `git status`;
- `git diff --stat`;
- whether Gold changed;
- whether Review Session semantics/schema changed;
- whether commit/push occurred;
- manual smoke steps still required.

Do not say a task is complete if required manual acceptance has not happened.
Say implementation/tests are complete and clearly mark remaining user smoke validation.

## 17. Architecture documents are authoritative

If this file and a more specific architecture document appear to disagree, STOP and inspect the current repository documentation rather than guessing.

The detailed normative sources remain:

```text
docs/architecture/v2.md
docs/architecture/guardrails.md
```

This `AGENTS.md` is an operational summary for coding agents, not a replacement for the architecture documentation.
