"""Create an approval-only terminology draft with the local Qwen model."""
from __future__ import annotations

import json
from pathlib import Path

from .ollama import chat
from .text import clean_story_text


def _excerpt(path: Path, limit: int = 3500) -> str:
    return clean_story_text(path.read_text(encoding="utf-8"))[:limit]


def extract_candidates(chapters: list[tuple[Path, Path, Path]], output: Path, model: str = "qwen3:14b") -> dict:
    entries: list[dict] = []
    for number, (zh_path, en_path, ru_path) in enumerate(chapters, start=1):
        prompt = f"""Extract only important recurring Lord of the Mysteries terminology from these parallel excerpts.
Return JSON with exactly one key: "terms". Its value is an array of 3–15 objects with keys:
zh, en, ru, category (character/place/organization/title/term/object), confidence (high/medium), note_ru.
Use only mappings visible in the excerpts. Do not invent mappings. Russian is fan_75 and is only a candidate.

CHINESE:\n{_excerpt(zh_path)}\n\nENGLISH:\n{_excerpt(en_path)}\n\nRUSSIAN fan_75:\n{_excerpt(ru_path)}"""
        try:
            parsed = json.loads(chat(prompt, model=model, context=8192, max_predict=1800, timeout=300, json_mode=True))
            terms = parsed.get("terms", []) if isinstance(parsed, dict) else []
        except (json.JSONDecodeError, OSError, ValueError):
            terms = []
        for term in terms:
            if isinstance(term, dict) and all(term.get(key) for key in ("zh", "en", "ru")):
                entry = {key: term.get(key, "") for key in ("zh", "en", "ru", "category", "confidence", "note_ru")}
                entry["chapters"] = [number]
                entries.append(entry)

    merged: dict[tuple[str, str, str], dict] = {}
    for entry in entries:
        key = (entry["zh"], entry["en"], entry["ru"])
        if key in merged:
            merged[key]["chapters"] = sorted(set(merged[key]["chapters"] + entry["chapters"]))
        else:
            merged[key] = entry
    result = {"status": "draft_requires_manual_approval", "source": "local_qwen_candidate_extraction", "terms": list(merged.values())}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
