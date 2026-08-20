"""Semantic review of OCR candidates; never edits the OCR draft."""

from __future__ import annotations

import json
from pathlib import Path

from .ollama import chat


def review_ocr_candidates(review_path: Path, english_path: Path, output: Path, model: str = "qwen3:14b") -> dict[str, object]:
    review = json.loads(review_path.read_text(encoding="utf-8"))
    candidates = review.get("candidates", [])
    if not isinstance(candidates, list):
        raise ValueError("OCR review file has no candidates list")
    english = english_path.read_text(encoding="utf-8")
    compact = [
        {"id": index + 1, "line": item.get("line"), "ocr": item.get("cleaned"), "flags": item.get("reasons")}
        for index, item in enumerate(candidates)
        if isinstance(item, dict)
    ]
    items: list[object] = []
    errors: list[str] = []
    # Small batches prevent a truncated JSON response from discarding all
    # review work when the local model reaches its output limit.
    for offset in range(0, len(compact), 7):
        batch = compact[offset : offset + 7]
        prompt = (
            "You review Russian OCR fragments from a printed literary translation using the official English chapter only as meaning context. "
            "Do NOT rewrite the chapter and do NOT invent text. For every candidate return one JSON object with: "
            "id, verdict ('confident_fix'|'needs_photo_check'|'leave_as_is'), suggested_russian (empty unless confident_fix), reason_ru. "
            "Use confident_fix only for obvious character/space/punctuation OCR mistakes supported by context. "
            "If wording may be a legitimate translation choice, use leave_as_is. If a word is unclear, use needs_photo_check. "
            "Return JSON only: {\"items\":[...]}.\n\n"
            f"OCR candidates:\n{json.dumps(batch, ensure_ascii=False)}\n\n"
            f"Official English chapter:\n{english}"
        )
        try:
            answer = json.loads(chat(prompt, model=model, temperature=0.0, context=8192, timeout=600, json_mode=True, max_predict=1600))
            if isinstance(answer, dict) and isinstance(answer.get("items"), list):
                items.extend(answer["items"])
            else:
                errors.append(f"batch {offset // 7 + 1}: response has no items list")
        except Exception as error:
            errors.append(f"batch {offset // 7 + 1}: {error}")
    result = {
        "source_review": str(review_path),
        "english_reference": str(english_path),
        "model": model,
        "instructions": "Suggestions are review-only and do not modify official OCR.",
        "items": items,
        "errors": errors,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
