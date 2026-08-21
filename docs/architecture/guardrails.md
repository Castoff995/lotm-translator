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

Approved narrow Phase 1 amendment (Task 004): structured EPUB ingestion may use
EbookLib for container/spine/navigation discovery and strict lxml DOM parsing
to recover physical Paragraphs. It must freeze explicit chapter slices and DOM
provenance in a versioned artifact. It is not paragraph alignment and may not
invoke hints, Gold mutation, or Phase 3 evidence.

Approved Phase 1 source-identity amendment (Task 004A): a versioned Source
Chapter Map may classify EPUB navigation entries and map source-local numbering
to canonical `ChapterId`. The registered map is the frozen authority for that
source revision. Do not silently reassign a canonical chapter, treat `toc_index`
or a local number as canonical identity, replace a frozen map, infer universal
volume semantics, or migrate identities referenced by human Gold. Such a change
requires an explicit source revision/migration architecture decision.

Approved Phase 2 persistence amendment (Task 004.1): unfinished human review is
stored in ignored, versioned Review Sessions rather than tracked Gold. Ordinary
review actions autosave only the session; Gold changes only through explicit,
fully validated, optimistic and atomic publication. Sessions freeze normalized
source snapshots and the target Gold baseline. They must never silently remap
human decisions, reinterpret unsupported review semantics, reopen confirmed
Gold, or mix hints/glossary data into review truth.

Because Review Sessions are ignored by Git, a clean working tree is no longer
sufficient evidence that a source/Paragraph migration is safe. Before changing
normalized source bytes, paragraphization, source identity, canonical chapter
mapping, or stable Paragraph IDs, run the active-session discovery command and
check relevance to the affected source/chapter. A relevant active session is a
human-WIP guardrail and requires an explicit publish, discard/archive, or
approved migration decision first. An unrelated session does not automatically
block ordinary code or documentation work.

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
