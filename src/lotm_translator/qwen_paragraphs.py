"""Ask local Qwen to apply the project's Russian paragraph-boundary rules."""
from __future__ import annotations

import re
import json
from tempfile import TemporaryDirectory
from pathlib import Path

from .ollama import chat
from .sentence_units import split_russian_sentences


def _remove_mirnovel_footer(text: str) -> str:
    return re.sub(r"\n_{8,}\s*\n(?:Комната|Поддержать автора)\b[\s\S]*$", "", text).strip()


def _examples_text() -> str:
    path = Path("config/qwen_russian_paragraph_examples.json")
    examples = json.loads(path.read_text(encoding="utf-8"))
    return "\n\n".join(
        f"ПРИМЕР {item['id']}\nВХОД:\n{item['input']}\nВЫХОД:\n{item['output']}"
        for item in examples
    )


def split_with_qwen(source: Path, rules: Path, output: Path, model: str = "qwen3:14b") -> str:
    rules_text = rules.read_text(encoding="utf-8")
    examples_text = _examples_text()
    # Qwen must never rewrite literary text. It judges boundaries only; the
    # final prose is assembled verbatim from source-derived candidates.
    with TemporaryDirectory() as temp_dir:
        candidate_path = Path(temp_dir) / "candidates.txt"
        split_russian_sentences(source, candidate_path)
        candidates = [part.strip() for part in candidate_path.read_text(encoding="utf-8").split("\n\n") if part.strip()]

    numbered = "\n".join(f"{index + 1}. {text}" for index, text in enumerate(candidates))
    prompt = f"""Ты проверяешь границы абзацев русского художественного текста.

Следуй обязательному своду правил проекта:
---
{rules_text}
---

Обязательные примеры правильного поведения:
---
{examples_text}
---

Ниже уже есть кандидаты, каждый обычно является отдельным предложением.
По умолчанию каждый кандидат ДОЛЖЕН остаться отдельным абзацем. Разрешено
только указать номера кандидатов, которые надо склеить со следующим, когда
это одна неразрывная фраза по правилам: троеточие с продолжением, реплика и
авторская ремарка через длинное тире. Нельзя склеивать просто соседние
обычные законченные предложения. Не переписывай текст.

Верни строго JSON без Markdown: {{"merge_with_next": [номера]}}.
Пример: {{"merge_with_next": [12, 27]}} означает склеить 12+13 и 27+28.
Если склеек нет: {{"merge_with_next": []}}.

Кандидаты:
---
{numbered}
---"""
    raw = chat(
        prompt,
        model=model,
        temperature=0.0,
        context=8192,
        timeout=600,
        json_mode=True,
        max_predict=1000,
    )
    decision = json.loads(raw)
    merge_after = decision.get("merge_with_next", [])
    if not isinstance(merge_after, list) or any(not isinstance(item, int) or item < 1 or item >= len(candidates) for item in merge_after):
        raise ValueError("Qwen returned invalid paragraph-boundary JSON")
    proposed_merges = set(merge_after)

    # The model may judge meaning too broadly.  It may only join a source
    # candidate where the visible syntax itself says that speech continues.
    # Finished sentences are never eligible, even if Qwen proposes them.
    allowed_merges = {
        index
        for index in range(1, len(candidates))
        if candidates[index - 1].rstrip().endswith("…")
        and (candidates[index].lstrip().startswith("—") or candidates[index][:1].islower())
    }
    merge_after = proposed_merges & allowed_merges
    rejected_merges = sorted(proposed_merges - merge_after)
    paragraphs: list[str] = []
    index = 0
    while index < len(candidates):
        paragraph = candidates[index]
        while index + 1 < len(candidates) and (index + 1) in merge_after:
            index += 1
            paragraph += " " + candidates[index]
        paragraphs.append(paragraph)
        index += 1
    result = "\n\n".join(paragraphs)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result + "\n", encoding="utf-8")
    output.with_suffix(".decision.json").write_text(
        json.dumps({
            "candidate_count": len(candidates),
            "qwen_proposed_merge_with_next": sorted(proposed_merges),
            "accepted_merge_with_next": sorted(merge_after),
            "rejected_by_syntax_guard": rejected_merges,
            "paragraph_count": len(paragraphs),
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def split_direct_with_qwen(source: Path, rules: Path, output: Path, model: str = "qwen3:14b") -> str:
    """Create a comparison-only direct Qwen rendition; never use it as corpus input."""
    story = _remove_mirnovel_footer(source.read_text(encoding="utf-8"))
    prompt = f"""Разбей русский художественный текст на абзацы по обязательным правилам.

{rules.read_text(encoding="utf-8")}

Обязательные примеры правильного поведения:
---
{_examples_text()}
---

Сохрани слова, пунктуацию и порядок. Верни только текст; между абзацами одна
пустая строка. Не добавляй заголовков или пояснений.

Текст:
---
{story}
---"""
    result = _remove_mirnovel_footer(chat(
        prompt, model=model, temperature=0.0, context=8192, timeout=600, max_predict=6000
    ).strip())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result + "\n", encoding="utf-8")
    return result
