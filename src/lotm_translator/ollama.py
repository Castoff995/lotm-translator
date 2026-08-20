from __future__ import annotations

import json
from pathlib import Path
from urllib.request import Request, urlopen

from .text import clean_story_text


SYSTEM_PROMPT = """You are a literary translator for Lord of the Mysteries.
Translate Chinese source prose into fluent Russian. Preserve meaning, names,
tone, dialogue, paragraph breaks, and uncertainty. The English text is only a
meaning-checking reference: do not mention it and do not translate it literally.
Start exactly with the translation of the first Chinese story sentence. Return
only the Russian translation. Do not add a chapter title, footnotes,
explanations, translator notes, headings, markdown, or commentary."""


def _first_paragraphs(path: Path, paragraph_count: int) -> str:
    text = clean_story_text(path.read_text(encoding="utf-8"))
    if text.startswith("# "):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    paragraphs = [paragraph.strip() for paragraph in text.split("\n\n") if paragraph.strip()]
    return "\n\n".join(paragraphs[:paragraph_count])


def _format_glossary(path: Path) -> str:
    entries = json.loads(path.read_text(encoding="utf-8"))
    return "\n".join(f"{entry['zh']} | {entry['en']} | {entry['ru']}" for entry in entries)


def translate_sample(chinese_file: Path, english_file: Path, glossary_file: Path, output: Path, model: str, paragraph_count: int) -> str:
    chinese = _first_paragraphs(chinese_file, paragraph_count)
    english = _first_paragraphs(english_file, paragraph_count)
    glossary = _format_glossary(glossary_file)
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "options": {"temperature": 0.2, "num_ctx": 4096},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Required glossary (Chinese | English | Russian):\n{glossary}\n\nChinese source:\n{chinese}\n\nOfficial English reference:\n{english}"},
        ],
    }
    request = Request(
        "http://127.0.0.1:11434/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=300) as response:
        result = json.loads(response.read().decode("utf-8"))
    translation = clean_story_text(result["message"]["content"]).strip() + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(translation, encoding="utf-8")
    return translation
