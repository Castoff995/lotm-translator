# Architecture v2 Guardrails

Before changing code, verify that the request matches Architecture v2, the
current phase, module boundaries, the domain model, and provenance rules.

Do not implement a request automatically when it:

- mixes `Paragraph`, `AlignmentUnit`, and `TrainingBlock`;
- lets alignment determine semantic grouping;
- moves v2 domain behavior into deprecated v1 alignment modules;
- mutates immutable raw data or loses provenance;
- infers source authority from filenames or directories instead of manifests;
- silently deletes or rewrites source-like content during normalization;
- implements functionality assigned to a later phase;
- introduces substantial technical debt that obstructs later phases;
- silently changes an approved architecture decision for implementation ease.

When a request conflicts with these rules, the coding agent must stop before
editing, name the violated principle and technical consequences, propose a
compatible alternative, and wait for explicit approval if the user still wants
the deviation.

If Architecture v2 itself appears contradictory or materially inferior to a
discovered alternative, do not add a workaround or change it silently. Document
the concern, propose an architecture amendment, and wait for a decision.

Minor implementation details that preserve these boundaries do not require a
separate approval.

## Phase 0–2 prohibitions

Task 001 must not implement BGE/SONAR/AWESOME-align, Bertalign ensembles,
reranking, dynamic-programming alignment, candidate scoring, confidence colors,
Qwen alignment judgment, trilingual reconciliation, semantic grouping models,
production `TrainingBlock` builders, training JSONL export, or LoRA.

Approved narrow Phase 2 exception: the isolated `hints` subsystem may use
AWESOME-align or an equivalent specialized word aligner only for temporary
paragraph-internal lexical visualization between one displayed ZH Paragraph and
its local machine-generated EN hint. It remains prohibited for corpus/Paragraph
alignment, AlignmentUnit ranking or suggestion, Gold evidence, and cursor/Gold
mutation. `hints` has no Gold write dependency. Any broader use requires a new
Architecture Guardrail decision.
