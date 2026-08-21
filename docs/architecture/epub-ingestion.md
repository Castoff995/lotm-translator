# Structured EPUB ingestion

Structured EPUB is a Phase 1 source-recovery workflow:

```text
immutable EPUB package
-> package/spine + EPUB2 NCX or EPUB3 nav inspection
-> explicit chapter slice proposal
-> versioned DOM paragraphization artifact
-> validation and explicit registration/freeze
-> deterministic normalization with DOM provenance
```

It is not paragraph alignment. `Paragraph` remains a physical/editorial block
of one source. The workflow never invokes hints, embeddings, Gold mutation, or
Phase 3 code.

## Optional dependencies

Install `requirements-epub.txt`. Text, OCR, Gold and review workflows do not
import these dependencies. EPUB commands fail with a targeted installation
message when they are absent.

## Workflow

```powershell
.\.venv\Scripts\python.exe -m src.lotm_v2.cli epub-inspect book.epub
.\.venv\Scripts\python.exe -m src.lotm_v2.cli ingest-epub data/manifests/v2/en.json book.epub --chapter 1 --root .
.\.venv\Scripts\python.exe -m src.lotm_v2.cli epub-artifact-generate data/manifests/v2/en.json book.epub --chapter 1 --output data/manifests/v2/paragraphization/en/ch_0001_epub.json
.\.venv\Scripts\python.exe -m src.lotm_v2.cli epub-artifact-check data/manifests/v2/en.json data/manifests/v2/paragraphization/en/ch_0001_epub.json --chapter 1 --root .
.\.venv\Scripts\python.exe -m src.lotm_v2.cli epub-artifact-register data/manifests/v2/en.json data/manifests/v2/paragraphization/en/ch_0001_epub.json --chapter 1 --root .
.\.venv\Scripts\python.exe -m src.lotm_v2.cli normalize data/manifests/v2/en.json --chapter 1 --root .
```

When a publisher restarts chapter numbering in later volumes, generation stops
as ambiguous. A human selects the intended navigation entry with
`--toc-index N`; that choice is recorded in the artifact.

## Reading and block rules

- Spine order wins over manifest iteration order; `linear=no` and navigation
  documents are not story slices.
- One chapter may occupy one document, several contiguous documents, or an
  anchor-delimited portion of a shared document.
- `p`, `h1`–`h6`, `li`, and `figcaption` are strong physical candidates. A leaf
  `div/section/article/main/blockquote/aside` may be a candidate only when it
  has visible text and no more specific block descendant.
- Inline markup stays within its paragraph. `<br>` is a line break, not a new
  paragraph. Ruby base text is retained while `rt/rp` is excluded from readable
  narrative text and ruby presence remains metadata.
- Visible body headings and footnote bodies are retained and structurally
  classified. Pure `<hr>` is recorded as a structural event; no fabricated
  textual separator is created.
- Script, style, template, head and navigation containers are non-reading
  structure. No class-name or ML "main content" heuristic silently removes
  visible blocks.

## Freeze and provenance

The artifact contains no full chapter text. It stores the package checksum,
chapter segments, TOC provenance, and ordered paragraph fragment locators:
document href, spine index, deterministic local-name DOM path, tag/optional ID,
exact document checksum and canonical XML fragment checksum. Optional display
text offsets are reserved for deterministic sub-element spans.

Registration is the explicit freeze action. It validates exact structural
candidate coverage and records artifact path/version/checksum in the source
manifest. Normalization accepts only that registered artifact. Parser or source
drift therefore fails closed instead of changing which text an existing stable
Paragraph ID denotes.
