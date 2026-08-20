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
    for index, page in enumerate(selected):
        lines = page.read_text(encoding="utf-8").splitlines()
        cleaned: list[str] = []
        for line in lines:
            normalized = re.sub(r"[^А-Яа-яЁё]", "", line).lower()
            # Known running/chapter headers are not literary body text.  Keep
            # the verified title below instead of the OCR-corrupted copy.
            if "повелительтайн" in normalized:
                continue
            if index == 0 and "багров" in normalized:
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

    def replace_token(match: re.Match[str]) -> str:
        token = match.group(0)
        letters = [char for char in token if char.isalpha()]
        # Only convert a wholly lookalike Latin token.  Words such as "Pas"
        # remain untouched and are sent to review rather than guessed.
        if letters and all(char in _LOOKALIKE_CHARS for char in letters):
            return token.translate(_LATIN_LOOKALIKES)
        return token

    for number, raw in enumerate(raw_lines, start=1):
        line = raw.replace("|", "").replace("©", "").replace("®", " ")
        # Between letters these are OCR substitutes for a missing space, not a
        # Russian apostrophe.  Preserve word boundaries rather than joining.
        line = re.sub(r"(?<=[А-Яа-яЁё])['`\u2018\u2019](?=[А-Яа-яЁё])", " ", line)
        line = re.sub(r"[A-Za-zА-Яа-яЁё]+", replace_token, line)
        line = re.sub(r"[ \t]+", " ", line).strip()
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
        if re.search(r"\b[А-Яа-яЁё]+-[А-Яа-яЁё]+\b", line):
            reasons.append("hyphenated_word_requires_photo_check")
        if reasons:
            item = {"line": number, "raw": raw, "cleaned": line, "reasons": reasons}
            if number in line_map:
                item["source"] = line_map[number]
            candidates.append(item)

    text = "\n".join(cleaned_lines)
    text = re.sub(r"(?<=[А-Яа-яЁё])-[ \t]*\n[ \t]*(?=[А-Яа-яЁё])", "", text)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text.strip() + "\n", encoding="utf-8")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({"source": str(source), "candidates": candidates}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"lines": len(cleaned_lines), "candidates": len(candidates)}
