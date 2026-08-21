# Human Gold Review Tool

The reusable Phase 2 review tool captures human alignment truth for any Gold
Draft compatible with the current Architecture v2 Gold Schema. It contains no
aligner, embeddings, LLM, similarity score, recommendation, confidence label,
or automatic acceptance.

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
The previous paragraph and the next three provide context but are never selected
automatically.

For every language choose how many consecutive paragraphs belong to the next
unit. There is no hard group-size limit. Select `GAP` instead when that source
has no corresponding paragraph, then choose a structured reason and optionally
write a note. Press Preview, inspect the complete three-sided group, and press
Confirm. Only confirmation changes the Gold Draft.

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

## Save, resume and corrections

Every confirmed unit, disposition, boundary, undo, or rollback is written atomically to the
same Gold JSON. Closing the server loses no confirmed work. Reopen the same file
to resume from its validated paragraph prefix.

`Undo last action` removes the most recent AlignmentUnit or disposition and any
boundary that becomes invalid. `Rollback to AlignmentUnit N` explicitly keeps
the review history before and through N, deletes downstream units, dispositions
and boundaries, and lets the reviewer rebuild them. This
is safer than silently rewriting later stable unit references. Confirmed Gold
opens read-only; Task 003 intentionally provides no implicit reopen operation.

## Boundary review

Alignment correspondence and semantic grouping remain separate. For each pair
of adjacent confirmed alignment units, Boundary Review records only:

- `JOIN`: both units should later share one semantic TrainingBlock;
- `BREAK`: a semantic TrainingBlock boundary belongs between them.

The tool does not build TrainingBlocks.

## Validation and finish

Partial validation runs at open and before every save. It permits unfinished
text but requires valid references, sequential unit IDs, exactly one partial
fate per consumed paragraph, no duplicate or skipped paragraphs, contiguous
monotonic ranges, valid explicit GAPs/dispositions, prefix-consistent cursors,
and valid existing boundaries.

`Full Gold validation` rechecks source hashes and then invokes the authoritative
full validator. It requires every physical paragraph to have exactly one fate
(AlignmentUnit XOR ParagraphDisposition) and a JOIN or BREAK after every
internal unit. Success reports that the draft is structurally
complete but does not change `status` to `confirmed`; confirmation requires a
separate explicit human/architectural action.
