from __future__ import annotations

import re
import shutil
import subprocess
import json
from pathlib import Path

from .image_preprocess import prepare_book_page


SUPPORTED_IMAGES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}

_LATIN_LOOKALIKES = str.maketrans({
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К", "M": "М", "O": "О", "P": "Р", "T": "Т", "X": "Х", "Y": "У",
    "a": "а", "c": "с", "e": "е", "h": "н", "k": "к", "m": "м", "o": "о", "p": "р", "t": "т", "x": "х", "y": "у",
})
_LOOKALIKE_CHARS = set("ABCEHKMOPTXYacehkmoptxy")
_KNOWN_TRUE_HYPHENS = {
    "багрово-красная", "бледно-алой", "веб-новеллах", "е-гуна", "кто-то", "по-прежнему",
    "серо-белая", "серо-белые", "темно-красный", "темно-красным", "хе-хе", "что-то", "ярко-красный",
}
_KNOWN_WRAPPED_HYPHENS = {
    "благо-состоянием": "благосостоянием", "боль-шинства": "большинства", "дво-ре": "дворе",
    "драко-нам": "драконам", "есте-ственно": "естественно", "из-лучая": "излучая", "мучи-ла": "мучила",
    "на-стенная": "настенная", "необра-ботанного": "необработанного", "ору-жия": "оружия",
    "отшат-нулся": "отшатнулся", "по-думать": "подумать", "попытал-ся": "попытался",
    "предположе-ний": "предположений", "при-мечательная": "примечательная", "прижже-ны": "прижжены",
    "пульсирую-щему": "пульсирующему", "пульсирую-щую": "пульсирующую", "раз-вернулся": "развернулся",
    "ре-вольвер": "револьвер", "сила-ми": "силами", "сно-видений": "сновидений",
}
_RUSSIAN_DICTIONARY = None
_OCR_LEXICON = None
_RUSSIAN_MORPHOLOGY = None
_CYRILLIC_ALPHABET = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя"
# Apple Live Text occasionally reads the Cyrillic ``г`` as a Latin ``r``.
# This is an OCR-shape preference, not an unconditional replacement: the
# reconstructed word must still be confirmed by the Russian dictionary.
_PREFERRED_SINGLE_LATIN_REPAIRS = {"r": "г", "R": "Г"}


def _ocr_joinable_prefixes() -> set[str]:
    """Read project-specific proper-name roots allowed to cross a line wrap."""
    global _OCR_LEXICON
    if _OCR_LEXICON is None:
        path = Path(__file__).resolve().parents[2] / "config" / "ocr_lexicon.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            _OCR_LEXICON = {str(item).lower() for item in payload.get("joinable_prefixes", [])}
        except (OSError, json.JSONDecodeError):
            _OCR_LEXICON = set()
    return _OCR_LEXICON


def _russian_dictionary():
    """Load the bundled Hunspell dictionary once, on demand."""
    global _RUSSIAN_DICTIONARY
    if _RUSSIAN_DICTIONARY is None:
        try:
            from spylls.hunspell import Dictionary
            root = Path(__file__).resolve().parents[2] / "data" / "dictionaries" / "ru_RU" / "ru_RU"
            _RUSSIAN_DICTIONARY = Dictionary.from_files(str(root)) if root.with_suffix(".dic").is_file() else False
        except (ImportError, OSError):
            _RUSSIAN_DICTIONARY = False
    return _RUSSIAN_DICTIONARY or None


def _russian_morphology():
    """Load Russian morphology for word forms absent from the Hunspell list."""
    global _RUSSIAN_MORPHOLOGY
    if _RUSSIAN_MORPHOLOGY is None:
        try:
            import pymorphy3
            _RUSSIAN_MORPHOLOGY = pymorphy3.MorphAnalyzer()
        except ImportError:
            _RUSSIAN_MORPHOLOGY = False
    return _RUSSIAN_MORPHOLOGY or None


def _known_russian_word(word: str, dictionary) -> bool:
    if dictionary and dictionary.lookup(word.lower()):
        return True
    morphology = _russian_morphology()
    return bool(morphology and morphology.word_is_known(word.lower()))


def _known_hyphenated_word(word: str, dictionary) -> bool:
    """Hunspell can over-accept arbitrary hyphenated forms via affix rules."""
    if word.lower() in _KNOWN_TRUE_HYPHENS:
        return True
    morphology = _russian_morphology()
    if morphology:
        return morphology.word_is_known(word.lower())
    return bool(dictionary and dictionary.lookup(word.lower()))


def _repair_one_latin_letter_in_russian_word(token: str, dictionary) -> str:
    """Repair one foreign OCR letter only when the Russian result is unique."""
    latin_positions = [index for index, char in enumerate(token) if char.isascii() and char.isalpha()]
    if len(latin_positions) != 1 or not any(char.isalpha() and not char.isascii() for char in token):
        return token
    position = latin_positions[0]
    preferred = _PREFERRED_SINGLE_LATIN_REPAIRS.get(token[position])
    if preferred:
        preferred_candidate = token[:position] + preferred + token[position + 1:]
        if _known_russian_word(preferred_candidate, dictionary):
            return preferred_candidate
    replacements = _CYRILLIC_ALPHABET.upper() if token[position].isupper() else _CYRILLIC_ALPHABET.lower()
    matches = []
    for replacement in replacements:
        candidate = token[:position] + replacement + token[position + 1:]
        if _known_russian_word(candidate, dictionary):
            matches.append(candidate)
    return matches[0] if len(set(matches)) == 1 else token


def _fix_dictionary_confirmed_wraps(line: str) -> str:
    """Join a wrap only when its joined spelling is confirmed by the dictionary."""
    dictionary = _russian_dictionary()

    def replace(match: re.Match[str]) -> str:
        hyphenated = match.group(0)
        left, right = hyphenated.split("-", maxsplit=1)
        joined = left + right
        if left.lower() in _ocr_joinable_prefixes():
            return joined
        if not _known_hyphenated_word(hyphenated, dictionary) and _known_russian_word(joined, dictionary):
            return joined
        return hyphenated

    return re.sub(r"\b[А-Яа-яЁё]+-[А-Яа-яЁё]+\b", replace, line)


def _normalise_known_hyphens(line: str) -> str:
    for source, replacement in _KNOWN_WRAPPED_HYPHENS.items():
        line = re.sub(rf"\b{re.escape(source)}\b", replacement, line, flags=re.IGNORECASE)
    return line


def find_tesseract(explicit_path: Path | None = None) -> str:
    if explicit_path:
        if explicit_path.is_file():
            return str(explicit_path)
        raise FileNotFoundError(f"Tesseract was not found at {explicit_path}")
    executable = shutil.which("tesseract")
    if executable:
        return executable
    default = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    if default.is_file():
        return str(default)
    raise FileNotFoundError(
        "Tesseract is not installed. Install Tesseract OCR with the Russian language data, "
        "or pass --tesseract C:\\path\\to\\tesseract.exe."
    )


def clean_russian_ocr(text: str) -> str:
    """Conservative cleanup that never asks an LLM to invent missing text."""
    text = text.replace("\ufeff", "").replace("\r\n", "\n")
    cleaned_lines: list[str] = []
    for line in text.split("\n"):
        # Common Tesseract artefacts from a page border; remove only at a line
        # edge, never from the prose itself.
        line = line.strip(" \t|`~#_")
        cyrillic = len(re.findall(r"[А-Яа-яЁё]", line))
        letters = len(re.findall(r"[A-Za-zА-Яа-яЁё]", line))
        # Decorative fragments almost never contain a real Russian word.
        if cyrillic < 3 and (not line or not re.search(r"[А-Яа-яЁё]{2}", line)):
            continue
        if letters and cyrillic / letters < 0.35:
            continue
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)
    # Restore words split only by a line-wrap hyphen, not genuine punctuation.
    text = re.sub(r"(?<=[А-Яа-яЁё])-[ \t]*\n[ \t]*(?=[А-Яа-яЁё])", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def ocr_images(
    input_dir: Path,
    output_dir: Path,
    tesseract: str,
    psm: int = 6,
    preprocess: bool = True,
) -> list[Path]:
    images = sorted(path for path in input_dir.iterdir() if path.suffix.lower() in SUPPORTED_IMAGES)
    if not images:
        raise ValueError(f"No supported image files found in {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    prepared_dir = output_dir / "preprocessed"
    outputs: list[Path] = []
    for image in images:
        ocr_input = image
        if preprocess:
            ocr_input = prepare_book_page(image, prepared_dir / f"{image.stem}.png")
        temporary_base = output_dir / f".{image.stem}.tesseract"
        subprocess.run(
            [tesseract, str(ocr_input), str(temporary_base), "-l", "rus+eng", "--psm", str(psm)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        # Tesseract appends .txt to the exact output base.  Do not use
        # Path.with_suffix here: the deliberately hidden temporary basename
        # starts with a dot and would otherwise lose its ".tesseract" part.
        temporary_text = Path(f"{temporary_base}.txt")
        output = output_dir / f"{image.stem}.txt"
        output.write_text(clean_russian_ocr(temporary_text.read_text(encoding="utf-8")), encoding="utf-8")
        temporary_text.unlink()
        outputs.append(output)
    return outputs


def assemble_ocr_chapter(
    pages_dir: Path,
    output: Path,
    first_stem: str,
    last_stem: str,
    chapter: int,
    title: str,
    printed_pages: str,
) -> Path:
    """Join already OCRed consecutive pages without using an LLM to repair them."""
    pages = sorted(pages_dir.glob("*.txt"))
    selected = [path for path in pages if first_stem <= path.stem <= last_stem]
    if not selected or selected[0].stem != first_stem or selected[-1].stem != last_stem:
        raise ValueError(f"Could not find complete page range {first_stem}..{last_stem} in {pages_dir}")

    chunks: list[str] = []
    line_map: list[dict[str, object]] = []
    draft_line = 3  # title, then a blank line
    normalized_title = re.sub(r"[^А-Яа-яЁё]", "", title).lower()
    for index, page in enumerate(selected):
        lines = page.read_text(encoding="utf-8").splitlines()
        cleaned: list[str] = []
        for line in lines:
            # Apple Live Text may join a running header and the first prose
            # line. Strip the header prefix only; never discard its prose.
            line = re.sub(
                r"^\s*(?:\d+\s*)?(?:Е\s*ЮАНЬ?|ПОВЕЛИТЕЛЬ\s*ТА[ЙИ]Н)\s*(?:\d+\s*)?",
                "",
                line,
                flags=re.IGNORECASE,
            ).strip()
            normalized = re.sub(r"[^А-Яа-яЁё]", "", line).lower()
            # Known running/chapter headers are not literary body text.  Keep
            # the verified title below instead of the OCR-corrupted copy.
            if index == 0 and ("глава" in normalized or normalized == normalized_title):
                continue
            cleaned.append(line)
        chunk = "\n".join(cleaned).strip()
        if chunk:
            chunks.append(chunk)
            for page_line, _ in enumerate(cleaned, start=1):
                line_map.append({"draft_line": draft_line, "ocr_page": page.name, "ocr_page_line": page_line})
                draft_line += 1
            if index < len(selected) - 1:
                draft_line += 1  # blank separator between photographed pages

    output.parent.mkdir(parents=True, exist_ok=True)
    body = "\n\n".join(chunks)
    output.write_text(f"Глава {chapter}. {title}\n\n{body}\n", encoding="utf-8")
    manifest = {
        "chapter": chapter,
        "title": title,
        "printed_pages": printed_pages,
        "source_ocr_pages": [page.name for page in selected],
        "status": "draft_requires_photo_review",
        "note": "Created locally from OCR pages; no LLM correction was applied.",
    }
    output.with_suffix(".manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(".line_map.json").write_text(json.dumps(line_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def clean_ocr_chapter_for_review(source: Path, output: Path, report_path: Path) -> dict[str, int]:
    """Apply only mechanical OCR fixes and list the remaining dubious lines."""
    raw_lines = source.read_text(encoding="utf-8").splitlines()
    map_path = source.with_suffix(".line_map.json")
    line_map = {item.get("draft_line"): item for item in json.loads(map_path.read_text(encoding="utf-8"))} if map_path.exists() else {}
    cleaned_lines: list[str] = []
    candidates: list[dict[str, object]] = []
    dictionary = _russian_dictionary()

    # Apple Live Text occasionally inserts a cluster of 1–3-character page
    # artefacts between two normal book lines (for example ``BE / CH / C``).
    # A real prose word can be short, so remove only a *run* of such fragments;
    # standalone doubtful words remain available for review.
    short_fragment = re.compile(r"^[A-Za-zА-Яа-яЁё'`’]{1,3}$")
    fragment_runs: set[int] = set()
    run: list[int] = []
    for index, raw in enumerate(raw_lines):
        if short_fragment.fullmatch(raw.strip()):
            run.append(index)
        else:
            if len(run) >= 2:
                fragment_runs.update(run)
            run = []
    if len(run) >= 2:
        fragment_runs.update(run)

    def replace_token(match: re.Match[str]) -> str:
        token = match.group(0)
        letters = [char for char in token if char.isalpha()]
        # Only convert a wholly lookalike Latin token.  Words such as "Pas"
        # remain untouched and are sent to review rather than guessed.
        if letters and all(char in _LOOKALIKE_CHARS for char in letters):
            return token.translate(_LATIN_LOOKALIKES)
        repaired = _repair_one_latin_letter_in_russian_word(token, dictionary)
        if repaired != token:
            return repaired
        return token

    for number, raw in enumerate(raw_lines, start=1):
        if number - 1 in fragment_runs or re.fullmatch(r"\s*\d{1,4}\s*", raw):
            continue
        line = raw.replace("|", "").replace("©", "").replace("®", " ")
        # Between letters these are OCR substitutes for a missing space, not a
        # Russian apostrophe.  Preserve word boundaries rather than joining.
        line = re.sub(r"(?<=[А-Яа-яЁё])['`\u2018\u2019](?=[А-Яа-яЁё])", " ", line)
        line = re.sub(r"[A-Za-zА-Яа-яЁё]+", replace_token, line)
        line = _normalise_known_hyphens(line)
        line = _fix_dictionary_confirmed_wraps(line)
        line = re.sub(r"[ \t]+", " ", line).strip()
        # Decorative fragments often contain a letter plus punctuation, or no
        # Cyrillic at all (``Ш:``, ``Ч;``, ``）``, ``é-``). They are never
        # prose on their own. Do not treat ordinary short Russian words alike.
        if len(line) <= 3 and (
            not re.search(r"[А-Яа-яЁё]", line)
            or re.fullmatch(r"[А-ЯЁ]{1,2}[;:]", line)
        ):
            continue
        # A 2–3-letter isolated token with no vowel is not Russian prose
        # (e.g. ``Пс``/``СЛ`` produced from a page decoration).  Keep one-letter
        # prepositions such as «в» and «с» untouched.
        if re.fullmatch(r"[А-Яа-яЁё]{2,3}", line) and not re.search(r"[АЕЁИОУЫЭЮЯаеёиоуыэюя]", line):
            continue
        # OCR's single-character debris at a line edge never carries prose.
        line = re.sub(r"^[\s|`~#_]+", "", line)
        line = re.sub(r"[\s|`~#_]+$", "", line)
        if line:
            cleaned_lines.append(line)
        reasons: list[str] = []
        if re.search(r"[A-Za-z]", line):
            reasons.append("latin_letters_remain")
        if re.search(r"[#$%^&*_+=<>|]", line):
            reasons.append("ocr_symbols_remain")
        if re.search(r"[.!?…][А-ЯЁ]", line):
            reasons.append("missing_space_after_sentence")
        hyphen_words = [word.lower() for word in re.findall(r"\b[А-Яа-яЁё]+-[А-Яа-яЁё]+\b", line)]
        if any(not _known_hyphenated_word(word, dictionary) for word in hyphen_words):
            reasons.append("hyphenated_word_requires_photo_check")
        if reasons:
            # ``line`` is retained for opening the source photo; ``output_line``
            # is the actual position in the cleaned draft and must be used when
            # applying a reviewed correction.
            item = {"line": number, "output_line": len(cleaned_lines), "raw": raw, "cleaned": line, "reasons": reasons}
            if number in line_map:
                item["source"] = line_map[number]
            candidates.append(item)

    text = "\n".join(cleaned_lines)
    # Confirmed Apple Live Text artefact from the page-24/25 spread of the
    # official book: the print reads «о самих днях недели», not «днях не, не».
    text = re.sub(r"(?<=\bднях) не,\s*не\s*\n\s*(?=недели\b)", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=[А-Яа-яЁё])-[ \t]*\n[ \t]*(?=[А-Яа-яЁё])", "", text)
    # Joining a word split across two physical OCR lines changes later line
    # numbers. Resolve each candidate against the actual cleaned output in
    # order, rather than relying on its pre-join position.
    final_lines = text.strip().splitlines()
    search_from = 0
    for candidate in candidates:
        target = str(candidate.get("cleaned", ""))
        for position in range(search_from, len(final_lines)):
            if final_lines[position] == target:
                candidate["output_line"] = position + 1
                search_from = position + 1
                break
        else:
            candidate.pop("output_line", None)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text.strip() + "\n", encoding="utf-8")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({"source": str(source), "candidates": candidates}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"lines": len(cleaned_lines), "candidates": len(candidates)}


def apply_ocr_review(cleaned: Path, candidates_path: Path, qwen_review_path: Path, output: Path) -> dict[str, int]:
    candidates = json.loads(candidates_path.read_text(encoding="utf-8")).get("candidates", [])
    review = json.loads(qwen_review_path.read_text(encoding="utf-8")).get("items", [])
    by_id = {item.get("id"): item for item in review if isinstance(item, dict)}
    lines = cleaned.read_text(encoding="utf-8").splitlines()
    applied = manual = deleted = 0
    for index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            continue
        item = by_id.get(index, {})
        decision = item.get("review", {}).get("decision") if isinstance(item.get("review", {}), dict) else None
        replacement = item.get("review", {}).get("replacement", "") if isinstance(item.get("review", {}), dict) else ""
        suggestion = item.get("suggested_russian", "")
        chosen = replacement if decision == "manual_fix" else suggestion if decision == "approved" else ""
        line = candidate.get("output_line", candidate.get("line"))
        if chosen and isinstance(line, int) and 1 <= line <= len(lines):
            # In the review UI, the explicit marker 111 means that the whole
            # candidate line is non-prose (for example, a stray header) and
            # must be removed from the final chapter rather than inserted.
            if decision == "manual_fix" and str(chosen).strip() == "111":
                lines[line - 1] = ""
                deleted += 1
            else:
                lines[line - 1] = str(chosen)
            applied += 1
            manual += decision == "manual_fix"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return {"applied": applied, "manual": manual, "deleted": deleted}


def migrate_ocr_review_decisions(old_candidates_path: Path, old_qwen_path: Path, new_candidates_path: Path) -> dict[str, int]:
    """Keep manual OCR decisions when a deterministic filter removes candidates."""
    old_candidates = json.loads(old_candidates_path.read_text(encoding="utf-8")).get("candidates", [])
    review_payload = json.loads(old_qwen_path.read_text(encoding="utf-8"))
    old_items = review_payload.get("items", [])
    new_candidates = json.loads(new_candidates_path.read_text(encoding="utf-8")).get("candidates", [])

    def key(candidate: dict) -> tuple[str, str, tuple[str, ...]]:
        return (str(candidate.get("raw", "")), str(candidate.get("cleaned", "")), tuple(candidate.get("reasons", [])))

    available: list[tuple[dict, dict, bool]] = []
    for index, candidate in enumerate(old_candidates):
        if isinstance(candidate, dict) and index < len(old_items) and isinstance(old_items[index], dict):
            available.append((candidate, old_items[index], False))

    migrated: list[dict] = []
    missing = 0
    for index, candidate in enumerate(new_candidates, start=1):
        match_index = next(
            (position for position, (old_candidate, _item, used) in enumerate(available)
             if not used and isinstance(candidate, dict) and key(old_candidate) == key(candidate)),
            None,
        )
        # A dictionary may fix one word in a long candidate line while the
        # remaining issue and the user's decision are unchanged. In that
        # case, the raw OCR line is the stable identity.
        if match_index is None:
            raw = str(candidate.get("raw", "")) if isinstance(candidate, dict) else ""
            match_index = next(
                (position for position, (old_candidate, _item, used) in enumerate(available)
                 if not used and str(old_candidate.get("raw", "")) == raw),
                None,
            )
        if match_index is None:
            missing += 1
            continue
        _old_candidate, old_item, _used = available[match_index]
        available[match_index] = (_old_candidate, old_item, True)
        item = dict(old_item)
        item["id"] = index
        migrated.append(item)
    review_payload["items"] = migrated
    review_payload["migration_note"] = "Manual decisions retained after deterministic OCR filtering."
    old_qwen_path.write_text(json.dumps(review_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"migrated": len(migrated), "missing": missing}
