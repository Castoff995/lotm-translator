# Gold Format v1 Draft

Gold files use pretty-printed UTF-8 JSON. JSON was selected because it requires
no dependency, is deterministic and diff-friendly, and has unambiguous schema
types. YAML comments are attractive for editing, but parser differences and
implicit scalar conversion would weaken reproducibility.

A gold chapter contains version/status, normalized source references with
checksums, alignment units that reference stable paragraph IDs, explicit gaps,
and independent semantic boundaries. Paragraph text remains canonical in the
normalized chapter JSON; it is not copied into each alignment unit.

Generated pilot files use `status: draft`. A draft is never gold truth merely
because a tool generated it. Human reviewers add/correct alignment units and a
`JOIN` or `BREAK` boundary after every internal unit, run validation, and only
then may change status to `confirmed`.

Explicit gaps have a structured reason (`addition`, `omission`,
`translator_note`, or `edition_difference`) and an optional note. A fake empty
paragraph is prohibited.
