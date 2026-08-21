# Structured EPUB ingestion

Structured EPUB is a Phase 1 source-recovery workflow:

```text
immutable EPUB package
-> package/spine + EPUB2 NCX or EPUB3 nav inspection
-> registered Source Chapter Map resolves canonical chapter identity
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
.\.venv\Scripts\python.exe -m src.lotm_v2.cli epub-artifact-generate data/manifests/v2/en.json book.epub --chapter 1 --output data/manifests/v2/paragraphization/en/ch_0001_epub.json --root .
.\.venv\Scripts\python.exe -m src.lotm_v2.cli epub-artifact-check data/manifests/v2/en.json data/manifests/v2/paragraphization/en/ch_0001_epub.json --chapter 1 --root .
.\.venv\Scripts\python.exe -m src.lotm_v2.cli epub-artifact-register data/manifests/v2/en.json data/manifests/v2/paragraphization/en/ch_0001_epub.json --chapter 1 --root .
.\.venv\Scripts\python.exe -m src.lotm_v2.cli normalize data/manifests/v2/en.json --chapter 1 --root .
```

Before a Source Chapter Map exists, a human may select a navigation entry with
`--toc-index N` for inspection/draft work. For scalable ingestion, register the
source map first:

```powershell
.\.venv\Scripts\python.exe -m src.lotm_v2.cli chapter-map-generate data/manifests/v2/zh.json --output data/manifests/v2/chapter-maps/zh.json --root .
.\.venv\Scripts\python.exe -m src.lotm_v2.cli chapter-map-check data/manifests/v2/zh.json data/manifests/v2/chapter-maps/zh.json --root .
.\.venv\Scripts\python.exe -m src.lotm_v2.cli chapter-map-register data/manifests/v2/zh.json data/manifests/v2/chapter-maps/zh.json --root .
.\.venv\Scripts\python.exe -m src.lotm_v2.cli chapter-map-inspect data/manifests/v2/zh.json --root .
```

After registration, `--chapter` is canonical and resolves through the frozen
map. Any supplied `--toc-index` must match that entry. TOC index remains an
exact EPUB locator; numbering scope plus local number is source-facing
numbering; neither replaces canonical `ChapterId`. Details are in
[`chapter-identity.md`](chapter-identity.md).

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

When the source manifest registers a Chapter Map, artifact registration and
normalization also verify that the artifact's canonical chapter, TOC index,
label, href, and fragment match the frozen map entry. The map reference lives
at source level in the manifest, so normalized Paragraph serialization does not
churn merely to duplicate source-catalogue metadata.
