# v1 Legacy Baseline

`src/lotm_translator/` is frozen conceptually as the v1 prototype. It remains
available for current local workflows and later benchmark comparisons.

In particular, these implementations must not be incrementally converted into
v2 architecture:

- `embedding_alignment.py`
- `qwen_alignment.py`
- `sequential_alignment.py`
- `bertalign_adapter.py`

Safe reuse means copying a proven, layer-appropriate idea behind a new v2
interface with explicit provenance and tests. It does not mean importing v1
cleanup assumptions or mixing v2 domain objects into legacy algorithm code.

Task 001 reuses only the general idea of chapter-oriented text ingestion. It
does not import any v1 alignment, Qwen, BGE, Bertalign, audit, or aggressive
cleanup implementation.
