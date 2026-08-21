# Structured EPUB pilot — Chapter 1

This is the documented pre-freeze migration of the empty Chapter 1 pilot. At
the migration gate, Gold contained 0 AlignmentUnits, 0 ParagraphDispositions,
and 0 boundaries. Source IDs remain `zh` and `en` because no human Gold decision
had yet frozen their earlier TXT-derived paragraph identities.

| Source | EPUB | Old TXT paragraphs | Structured paragraphs | Artifact SHA-256 | Normalized SHA-256 |
|---|---:|---:|---:|---|---|
| ZH | EPUB 2.0 / NCX | 70 | 70 | `56c661bc4f0fd82986192709ca029510a5fe01ffbcf6c10920ecf9c01207a09b` | `ac3bd992e582783a335a97dbca5493eb78b1d03606d2823e3b949f6cc9093858` |
| EN | EPUB 3.0 / nav | 68 | 71 | `df8dd6240b627b205e0970d745c43c2bd65be7325e1b3af7634ff02636ffca77` | `789d93fed725fea45a78578911e1185f42c50c74fe963c5448be13055decd5b3` |

ZH content is identical after whitespace removal except that the old flattened
TXT had added a Markdown `#` before the body heading. EN structured extraction
preserves three physical blocks absent from the flattened TXT: two visible
edition navigation/link list items and one marked footnote body (454
non-whitespace characters total). These blocks were retained rather than
silently classified away; any Gold disposition remains a later human decision.

The ZH TOC contains three different volume-local entries numbered Chapter 1.
Automatic mapping therefore stopped as ambiguous. The pilot records explicit
TOC index `0`, resolving to the first book chapter. EN discovery was unique.

Raw EPUB packages are stored under ignored `data/raw/`; normalized outputs are
also ignored. Only manifests and text-free structural artifacts are tracked.
The RU official OCR normalized file was not regenerated or modified.
