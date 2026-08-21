# Architecture v2 Roadmap

| Phase | Goal | Exit criterion |
|---|---|---|
| 0 — Freeze v1 | Preserve v1 as prototype and benchmark baseline. | v1 is labelled legacy; its workflows still run and v2 has a separate namespace. |
| 1 — Foundation | Typed sources, stable paragraphs, immutable ingest, provenance and normalization. | A pilot chapter can be ingested and normalized deterministically with schema round-trip. |
| 2 — Gold format | Human-editable gold alignment, explicit gaps, independent boundaries and validation. | Artificial positive/negative tests pass and two pilot drafts can be created without calling them truth. |
| 3 — Alignment Core | Produce pairwise alignment candidates without semantic grouping. | Candidate alignment works against frozen pilot gold metrics. |
| 4 — Reranker + Bertalign ensemble | Combine complementary alignment evidence. | Ensemble improves agreed metrics without changing gold truth. |
| 5 — Review subsystem | Present confidence evidence and capture human alignment decisions. | Review decisions are reproducible, provenance-preserving and validated. |
| 6 — 20 gold chapters | Build a representative manually confirmed benchmark. | Twenty chapters pass gold validation and review. |
| 7 — Trilingual reconciliation | Reconcile ZH↔EN and ZH↔RU with EN↔RU consistency. | Confirmed trilingual units retain all pairwise provenance. |
| 8 — Semantic grouping | Learn/predict `JOIN`/`BREAK` after alignment. | Grouping is evaluated separately against gold boundary labels. |
| 9 — Dataset export | Apply quality gates and export training examples. | Deterministic dataset build passes coverage and provenance gates. |
| 10 — LoRA | Train and evaluate the local translation adapter. | Training is reproducible and evaluated against held-out gold data. |
