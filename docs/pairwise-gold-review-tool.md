# Pairwise Gold Review Tool

Launch the dedicated two-source reviewer separately from the trilingual Human
Gold Review Tool:

```powershell
.\.venv\Scripts\python.exe -m src.lotm_v2.pairwise_review `
  data\gold\v2\pairwise\zh--en\ch_0001.json `
  --bootstrap-from data\gold\v2\ch_0001.json
```

It displays exactly ZH+EN or ZH+RU. The human selects consecutive counts for
both sides, or one human GAP, then uses Preview and Confirm. ParagraphDisposition
advances only its selected source cursor. No JOIN/BREAK controls exist.

The optional trilingual panel is visibly non-authoritative and displays the
third language only as read-only context. `Use remaining bootstrap span` fills
count/GAP controls but mutates nothing; Preview and Confirm remain mandatory.

Forward decisions autosave only the ignored Pairwise Review Session:

```text
data/review_sessions/v2/pairwise/<work_id>/<left>--<right>/ch_NNNN.active.json
```

Pairwise Review Session schema `1.1-draft`, semantics `1.1`, freezes the target
Pairwise Gold path/base byte hash, exact source snapshots, optional bootstrap
path/hash, baseline Pairwise Gold, monotonic revision, persistent event journal,
request-id history, and verified working Pairwise Gold cache. Every successful
semantic mutation supplies `expected_revision` and a UUID `request_id`, rereads
the session under an OS lock, compare-and-swaps the on-disk revision, appends
one canonical semantic event, increments the revision exactly once, and writes
atomically. Identical request retries are idempotent; reuse for another action
fails closed.

Every semantic Preview also supplies the revision visible to that client. The
server compares it under the same session lock before structural validation or
candidate registration. A stale client receives an explicit revision conflict
and cannot overwrite a still-valid concurrent human decision.

Resume replays the complete journal from the immutable session baseline and
requires the replayed document to equal persisted working Gold. Undo removes
exactly the latest journalled semantic mutation and replays the remainder, so
Confirm, disposition, Rollback, Split, Merge, and disposition edits undo
correctly after restart. Existing `1.0-draft` Pairwise sessions are metadata-
inspectable but never auto-migrated or opened writable; they require an explicit
discard/archive or separately approved migration decision.

Preview is server-owned and candidate-bound. A successful semantic preview
returns an opaque token bound to session ID, revision, canonical action payload,
candidate Gold hash, and expiry. Confirm sends only the token, a request ID,
and expected revision. It cannot substitute changed client controls. Tokens
expire and fail closed after revision change, another server generation,
restart, unknown token, or timeout. Unit, disposition, Split, Merge,
disposition-edit, and Rollback previews use this invariant.

The HTML dialog shows a scrollable concise old-to-new/selection preview with a
sticky action footer and local error area. Mutation controls are disabled while
a request is pending, repeated clicks are ignored, Escape cancels when idle,
and Enter never implicitly confirms a stale or pending preview.

Available lifecycle actions are Preview, Confirm unit, GAP, disposition, Undo,
Rollback, Split, Merge with next, Edit disposition, full validation, Publish,
Discard and Resume. Publication requires explicit confirmation and full
validation, atomically writes only `status=draft`, and archives the session.
All session mutations use the session OS lock. Publication uses the global
deadlock-safe order `(1) session lock, (2) target Pairwise Gold lock`; CLI Gold
confirmation takes only that same target lock. Under both publication locks the
service reloads the session, checks revision/request ID and baseline target
bytes, validates, writes, and verifies before releasing either resource.

Terminal persistence writes and verifies the final archive first, then removes
the active file while the session lock is held. If a crash leaves both, startup
requires matching session/baseline/source/journal/working state, treats the
terminal archive as authoritative, and removes only the stale active copy.
Publication intent plus canonical document and exact file hashes recovers a
crash before target write, after target write, or before archive finalization
without resuming already-published work as writable.

The verified target-scoped terminal archive is also the durable request receipt
for Publish and Discard. After restart it is checked before any replacement
active session is created. An exact request-ID/action/payload retry returns the
recorded result without repeating the terminal effect; semantic request-ID reuse
and malformed or target-mismatched archives fail closed. A new legitimate action
may lazily create a new active session under the normal session lock.

Crash consistency uses the existing terminal order rather than a second
authority artifact. Publish persists intent, atomically writes and verifies the
target Gold, then atomically writes/verifies the terminal archive before removing
the active session; startup can finish the archive from a matching intent and
target hash. Discard atomically writes/verifies its terminal archive (the action
and receipt) before removing the active session. A crash cannot expose a receipt
for an action that did not reach its terminal effect.

Benchmark confirmation is not a Reviewer action. After human publication it is
performed separately with `pairwise-gold-confirm` and the expected canonical
document and exact file SHA-256 values. A confirmed artifact reopens read-only.
