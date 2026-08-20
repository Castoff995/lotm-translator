from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from .ollama import chat


def _paragraphs(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").split("\n\n") if line.strip() and not line.startswith("# ")]


def _files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob("*.txt") if p.is_file())


def _window_size(*paragraph_groups: list[str]) -> int:
    """Keep prompts small for chapters likely to exceed the local model timeout."""
    paragraph_count = max(len(group) for group in paragraph_groups)
    longest_paragraph = max((len(paragraph) for group in paragraph_groups for paragraph in group), default=0)
    return 5 if paragraph_count >= 80 or longest_paragraph >= 1_200 else 8


def _write_progress(output: Path, **values: object) -> None:
    (output / "progress.json").write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ollama_check() -> dict[str, object]:
    try:
        with urlopen("http://127.0.0.1:11434/api/ps", timeout=5) as response:
            models = json.loads(response.read().decode("utf-8")).get("models", [])
        return {"reachable": True, "loaded_models": [item.get("name") for item in models]}
    except (OSError, URLError, TimeoutError) as error:
        return {"reachable": False, "error": str(error)}


def _review_required(output: Path, chapter: int, window: int, attempts: list[dict[str, object]]) -> None:
    payload = {
        "status": "review_required",
        "reason": "No usable Qwen response after three attempts",
        "chapter": chapter,
        "window": window,
        "attempts": attempts,
        "action": "Stop the queue and ask the operator to review Ollama/model state before resuming.",
    }
    (output / "REVIEW_REQUIRED.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_progress(output, **payload)


def _normalise_alignment(value: object) -> object:
    if isinstance(value, dict) and isinstance(value.get("alignments"), list):
        return value["alignments"]
    if isinstance(value, dict) and all(isinstance(item, dict) for item in value.values()):
        return list(value.values())
    return value


def prepare_alignment(zh_dir: Path, en_dir: Path, ru_dir: Path, output: Path, model: str, start_chapter: int = 1, end_chapter: int | None = None) -> int:
    sources = (_files(zh_dir), _files(en_dir), _files(ru_dir))
    count = min(map(len, sources))
    if count < 1:
        raise ValueError("No matching chapter files found")
    output.mkdir(parents=True, exist_ok=True)
    final_chapter = min(count, end_chapter or count)
    for chapter_index in range(count):
        if chapter_index + 1 < start_chapter or chapter_index + 1 > final_chapter:
            continue
        zh, en, ru = (_paragraphs(group[chapter_index]) for group in sources)
        window = _window_size(zh, en, ru)
        windows = []
        for offset in range(0, max(len(zh), len(en), len(ru)), window):
            window_number = len(windows) + 1
            total_windows = (max(len(zh), len(en), len(ru)) + window - 1) // window
            _write_progress(
                output,
                status="running",
                chapter=chapter_index + 1,
                window=window_number,
                total_windows=total_windows,
                window_size=window,
                paragraph_counts={"zh": len(zh), "en": len(en), "ru": len(ru)},
            )
            prompt = (
                "You align literary paragraphs. Return JSON only in exactly this form: {\"alignments\":[{\"zh\":[1],\"en\":[1],\"ru\":[1],\"note\":\"\"}]}. "
                "Each value except note is an array of local paragraph numbers. Preserve all meaning; do not translate or rewrite. "
                "Use empty arrays only for genuine omissions.\n\n"
                f"Chinese paragraphs (numbered from {offset + 1}):\n" + "\n".join(f"{i}: {p}" for i, p in enumerate(zh[offset:offset + window], offset + 1)) +
                f"\n\nEnglish paragraphs (numbered from {offset + 1}):\n" + "\n".join(f"{i}: {p}" for i, p in enumerate(en[offset:offset + window], offset + 1)) +
                f"\n\nRussian paragraphs (numbered from {offset + 1}):\n" + "\n".join(f"{i}: {p}" for i, p in enumerate(ru[offset:offset + window], offset + 1))
            )
            attempts: list[dict[str, object]] = []
            aligned: object | None = None
            duration_seconds = 0.0
            for attempt in range(1, 4):
                started = time.monotonic()
                try:
                    raw = chat(prompt, model=model, temperature=0.0, context=4096, timeout=120, json_mode=True)
                    candidate = json.loads(raw)
                except (TimeoutError, URLError, json.JSONDecodeError) as error:
                    duration_seconds = round(time.monotonic() - started, 1)
                    attempts.append({"attempt": attempt, "duration_seconds": duration_seconds, "error": str(error), "ollama_check": _ollama_check()})
                    _write_progress(output, status="retrying", chapter=chapter_index + 1, window=window_number, total_windows=total_windows, attempt=attempt, attempts_left=3 - attempt, last_failure=attempts[-1])
                    continue
                duration_seconds = round(time.monotonic() - started, 1)
                aligned = _normalise_alignment(candidate)
                break
            if aligned is None:
                _review_required(output, chapter_index + 1, window_number, attempts)
                raise RuntimeError(f"Qwen stalled three times in chapter {chapter_index + 1}, window {window_number}; review required")
            windows.append({"offset": offset + 1, "duration_seconds": duration_seconds, "slow": duration_seconds >= 120, "attempts_before_success": attempts, "alignment": aligned})
        payload = {
            "chapter": chapter_index + 1,
            "zh_file": sources[0][chapter_index].name,
            "en_file": sources[1][chapter_index].name,
            "ru_file": sources[2][chapter_index].name,
            "status": "qwen_assisted_needs_review",
            "detector": {
                "window_size": window,
                "paragraph_counts": {"zh": len(zh), "en": len(en), "ru": len(ru)},
                "slow_request_threshold_seconds": 120,
                "slow_windows": sum(1 for item in windows if item["slow"]),
            },
            "windows": windows,
        }
        (output / f"ch_{chapter_index + 1:04d}_alignment.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _write_progress(output, status="chapter_complete", chapter=chapter_index + 1, windows=len(windows), slow_windows=payload["detector"]["slow_windows"])
    _write_progress(output, status="complete", chapters=final_chapter - start_chapter + 1)
    return final_chapter - start_chapter + 1
