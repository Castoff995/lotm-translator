# Architecture v2 Roadmap

| Phase | Goal | Exit criterion |
|---|---|---|
| 0 — Freeze v1 | Preserve v1 as prototype and benchmark baseline. | v1 is labelled legacy; its workflows still run and v2 has a separate namespace. |
| 1 — Foundation | Typed sources, stable paragraphs, immutable ingest, provenance and normalization. | A pilot chapter can be ingested and normalized deterministically with schema round-trip. |
| 2 — Gold format | Human-editable gold alignment, explicit gaps, independent boundaries, validation, and a human-only reusable annotation UI. | Synthetic positive/negative tests pass; compatible drafts can be resumed and edited without inference or calling drafts truth. |
| 3 — Alignment Core | Produce pairwise alignment candidates without semantic grouping. | Candidate alignment works against frozen pilot gold metrics. |
| 4 — Reranker + Bertalign ensemble | Combine complementary alignment evidence. | Ensemble improves agreed metrics without changing gold truth. |
| 5 — Model-assisted review subsystem | Present Phase 3–4 confidence evidence without auto-accepting it and capture human decisions. | Review decisions are reproducible, provenance-preserving and validated. |
| 6 — 20 gold chapters | Build a representative manually confirmed benchmark. | Twenty chapters pass gold validation and review. |
| 7 — Trilingual reconciliation | Reconcile ZH↔EN and ZH↔RU with EN↔RU consistency. | Confirmed trilingual units retain all pairwise provenance. |
| 8 — Semantic grouping | Learn/predict `JOIN`/`BREAK` after alignment. | Grouping is evaluated separately against gold boundary labels. |
| 9 — Dataset export | Apply quality gates and export training examples. | Deterministic dataset build passes coverage and provenance gates. |
| 10 — LoRA | Train and evaluate the local translation adapter. | Training is reproducible and evaluated against held-out gold data. |

Task 004 is a narrow Phase 1 amendment discovered during the Phase 2 pilot. It
adds structured EPUB ingestion and DOM provenance only; it does not start Phase
3 or make paragraph-correspondence decisions.

Task 004A is a second narrow Phase 1 amendment. It freezes source navigation to
canonical corpus chapter identity before EPUB paragraphization. Source-local
numbering resets and ancillary navigation entries remain source metadata, not
Paragraph alignment or Gold decisions.
