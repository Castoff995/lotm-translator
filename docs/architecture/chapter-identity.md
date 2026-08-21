# Canonical chapter identity

Chapter identity is resolved during Phase 1 source cataloguing, before any
paragraph alignment. `ChapterId(work_id, number)` always names a canonical,
language-independent corpus chapter. Its number is not an EPUB TOC position,
spine index, file name, or publisher-local chapter number.

## Coordinates

One source chapter may have several distinct coordinates:

- `canonical_chapter` is the global corpus number used by `ChapterId` and by
  stable Paragraph IDs;
- `source_chapter_ordinal` is the 1-based order of narrative chapter entries in
  this source and excludes ancillary navigation entries;
- `numbering_scope` is the 1-based contiguous numbering regime in the source;
- `local_chapter_number` is the number printed by that edition and may be null;
- `toc_index`, `toc_label`, `toc_href`, and `toc_fragment` are exact physical
  navigation locators and metadata, not canonical identity.

`numbering_scope` deliberately does not mean `volume`. A scope may reflect a
book, part, section, arc, or an otherwise unexplained numbering reset. Optional
publisher terminology can be recorded separately without changing identity.

Paragraph IDs remain derived from work, source, canonical chapter, and physical
paragraph index. Source-local numbering and TOC coordinates never enter the ID.

## Source Chapter Map

A versioned Source Chapter Map answers one question: which source navigation
entry represents which canonical corpus chapter? Every navigation entry in the
selected immutable EPUB package is classified exactly once as:

- `chapter`, with explicit canonical and source-local coordinates;
- `ancillary`, with a structured kind and optional explanation;
- `ambiguous`, when a draft cannot safely classify it.

A confirmed map cannot contain `ambiguous` entries. `ancillary` is source
cataloguing only: it does not delete XHTML, create Gold, or imply a
`ParagraphDisposition`.

The map contains TOC metadata and hashes, never chapter body text. It is tied to
the exact raw EPUB SHA-256 and navigation kind. Validation compares every map
entry with the inspected EPUB navigation and fails closed on hash, count,
ordering, label, href, fragment, uniqueness, or identity drift.

## Workflow and freeze

```text
inspect EPUB
-> generate draft Source Chapter Map
-> validate and review every entry
-> register confirmed map
-> resolve canonical chapter to TOC locator
-> create/register EPUB paragraphization artifact
```

Registration records map path, SHA-256, and version in the source manifest.
Those values are frozen for that source revision. A replacement requires an
explicit new map version; if it would reassign an already frozen canonical
chapter, Paragraph identity, or human Gold reference, processing stops for a
source-revision/migration architecture decision.

After registration, canonical lookup is authoritative. A manual `toc_index`
that contradicts the registered map is rejected. Before a map exists, explicit
TOC selection remains available for inspection and draft work only.

The EPUB paragraphization artifact keeps its canonical `chapter` and exact TOC
locator. Registration and normalization validate that pair against the source
manifest's frozen Chapter Map. This external, hash-bound link avoids changing
the artifact or normalized Paragraph serialization merely to duplicate map
metadata.

## Layer boundary

Chapter mapping uses only immutable source structure, navigation order,
explicit number labels, and human confirmation. It does not use translation,
embeddings, semantic similarity, hints, AlignmentUnits, Gold mutation, or
TrainingBlocks. Phase 3 therefore receives normalized chapters whose canonical
identity has already been resolved.
