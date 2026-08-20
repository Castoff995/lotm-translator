from __future__ import annotations

import json
from pathlib import Path
from urllib.error import URLError

from .ollama import chat


def _files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.txt"))


def _paragraphs(path: Path) -> list[str]:
    return [item.strip() for item in path.read_text(encoding="utf-8").split("\n\n") if item.strip() and not item.startswith("# ")]


def _numbered(items: list[str], start: int) -> str:
    return "\n".join(f"{index}: {item}" for index, item in enumerate(items, start))


def _valid(result: object, available: dict[str, int]) -> tuple[bool, list[dict[str, list[int]]]]:
    if not isinstance(result, dict) or not isinstance(result.get("alignments"), list):
        return False, []
    rows = result["alignments"]
    seen = {language: [] for language in available}
    clean: list[dict[str, list[int]]] = []
    for row in rows:
        if not isinstance(row, dict):
            return False, []
        current: dict[str, list[int]] = {}
        note = row.get("note")
        for language, limit in available.items():
            numbers = row.get(language)
            if not isinstance(numbers, list) or len(numbers) > 3 or not all(isinstance(value, int) and 1 <= value <= limit for value in numbers):
                return False, []
            if not numbers and not (isinstance(note, str) and note.strip().startswith("unmatched:")):
                return False, []
            current[language] = numbers
            seen[language].extend(numbers)
        clean.append(current)
    if not clean:
        return False, []
    # A valid response consumes the same prefix of each supplied language exactly once.
    for language, values in seen.items():
        if sorted(values) != list(range(1, max(values) + 1)) or len(values) != len(set(values)):
            return False, []
    return True, clean


def align_sequential(zh_dir: Path, en_dir: Path, ru_dir: Path, output: Path, model: str, chapter_count: int = 3) -> int:
    source_files = (_files(zh_dir), _files(en_dir), _files(ru_dir))
    count = min(min(len(files) for files in source_files), chapter_count)
    if count < 1:
        raise ValueError("No matching chapter files found")
    output.mkdir(parents=True, exist_ok=True)
    for chapter_index in range(count):
        texts = {"zh": _paragraphs(source_files[0][chapter_index]), "en": _paragraphs(source_files[1][chapter_index]), "ru": _paragraphs(source_files[2][chapter_index])}
        cursors = {language: 0 for language in texts}
        records: list[dict[str, list[int]]] = []
        while any(cursors[language] < len(texts[language]) for language in texts):
            if any(cursors[language] >= len(texts[language]) for language in texts):
                raise RuntimeError(f"Uneven source exhaustion in chapter {chapter_index + 1}; review required")
            snippets = {language: texts[language][cursors[language]:cursors[language] + 8] for language in texts}
            available = {language: len(snippets[language]) for language in texts}
            prompt = (
                "Align the NEXT paragraphs of one literary chapter in Chinese, official English, and Russian. "
                "Return JSON only: {\"alignments\":[{\"zh\":[1,2],\"en\":[1,2],\"ru\":[1],\"note\":\"\"}]}. "
                "Numbers are LOCAL to this displayed block. Align by meaning, never just by equal number. "
                "You may use many-to-one, one-to-many, or many-to-many groups. If a language truly has no matching paragraph, use an empty array only with note starting exactly `unmatched:` and explain why; otherwise group merged paragraphs. "
                "Use no more than three paragraphs of any language in one unit. Every consumed local paragraph must occur exactly once. "
                "You may consume different-length prefixes of the three languages; do not include paragraphs after a consumed prefix.\n\n"
                f"ZH:\n{_numbered(snippets['zh'], 1)}\n\nEN:\n{_numbered(snippets['en'], 1)}\n\nRU:\n{_numbered(snippets['ru'], 1)}"
            )
            rows: list[dict[str, list[int]]] = []
            failures: list[dict[str, object]] = []
            for attempt in range(1, 4):
                try:
                    raw = chat(prompt, model=model, temperature=0.0, context=4096, timeout=120, json_mode=True)
                    result = json.loads(raw)
                    valid, rows = _valid(result, available)
                    if not valid:
                        failures.append({"attempt": attempt, "reason": "invalid_alignment_schema_or_coverage", "raw": raw})
                except (TimeoutError, URLError, json.JSONDecodeError) as error:
                    valid = False
                    failures.append({"attempt": attempt, "reason": str(error)})
                if valid:
                    break
            if not rows:
                (output / "REVIEW_REQUIRED.json").write_text(
                    json.dumps({"chapter": chapter_index + 1, "cursors": cursors, "available": available, "failures": failures}, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                raise RuntimeError(f"Qwen returned invalid alignment three times in chapter {chapter_index + 1}; review required")
            for row in rows:
                records.append({language: [cursors[language] + number for number in row[language]] for language in texts})
            for language in texts:
                cursors[language] += max(number for row in rows for number in row[language])
            (output / "progress.json").write_text(json.dumps({"status": "running", "chapter": chapter_index + 1, "paragraphs_consumed": cursors, "paragraph_counts": {language: len(texts[language]) for language in texts}}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report = {"chapter": chapter_index + 1, "status": "coverage_validated", "paragraph_counts": {language: len(texts[language]) for language in texts}, "alignments": records}
        (output / f"ch_{chapter_index + 1:04d}_alignment.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "progress.json").write_text(json.dumps({"status": "complete", "chapters": count}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return count
