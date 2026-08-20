from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path


SUPPORTED_IMAGES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}


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
    # Restore words split only by a line-wrap hyphen, not genuine punctuation.
    text = re.sub(r"(?<=[А-Яа-яЁё])-[ \t]*\n[ \t]*(?=[А-Яа-яЁё])", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def ocr_images(input_dir: Path, output_dir: Path, tesseract: str, psm: int = 6) -> list[Path]:
    images = sorted(path for path in input_dir.iterdir() if path.suffix.lower() in SUPPORTED_IMAGES)
    if not images:
        raise ValueError(f"No supported image files found in {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for image in images:
        temporary_base = output_dir / f".{image.stem}.tesseract"
        subprocess.run(
            [tesseract, str(image), str(temporary_base), "-l", "rus+eng", "--psm", str(psm)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        temporary_text = temporary_base.with_suffix(".txt")
        output = output_dir / f"{image.stem}.txt"
        output.write_text(clean_russian_ocr(temporary_text.read_text(encoding="utf-8")), encoding="utf-8")
        temporary_text.unlink()
        outputs.append(output)
    return outputs
