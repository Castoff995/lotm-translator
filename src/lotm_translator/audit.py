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
    return "\n".join(f"{index}: {value}" for index, value in enumerate(items, start))


def _normalise_rows(value: object) -> list[dict[str, object]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        if isinstance(value.get("alignments"), list):
            return [item for item in value["alignments"] if isinstance(item, dict)]
        return [item for item in value.values() if isinstance(item, dict)]
    return []


def _joined(paragraphs: list[str], numbers: object) -> str:
    if not isinstance(numbers, list):
        return ""
    return "\n\n".join(paragraphs[number - 1] for number in numbers if isinstance(number, int) and 1 <= number <= len(paragraphs))


def _embedding_units(zh_en_path: Path, en_ru_path: Path) -> list[dict[str, list[int]]]:
    """Join two BGE pair alignments through their shared English paragraphs."""
    zh_en = json.loads(zh_en_path.read_text(encoding="utf-8")).get("alignments", [])
    en_ru = json.loads(en_ru_path.read_text(encoding="utf-8")).get("alignments", [])
    if not isinstance(zh_en, list) or not isinstance(en_ru, list):
        raise ValueError("Invalid BGE alignment JSON")

    # Make connected components. This avoids splitting or duplicating a Russian
    # group when an English paragraph group crosses a BGE grouping boundary.
    seen_zh: set[int] = set()
    units: list[dict[str, list[int]]] = []
    for start in range(len(zh_en)):
        if start in seen_zh:
            continue
        pending_zh, pending_ru = [start], []
        component_zh: set[int] = set()
        component_ru: set[int] = set()
        english: set[int] = set()
        while pending_zh or pending_ru:
            while pending_zh:
                index = pending_zh.pop()
                if index in component_zh:
                    continue
                component_zh.add(index)
                values = zh_en[index].get("right", [])
                english.update(value for value in values if isinstance(value, int))
                for other, row in enumerate(en_ru):
                    right_values = row.get("left", [])
                    if any(value in english for value in right_values if isinstance(value, int)) and other not in component_ru:
                        pending_ru.append(other)
            while pending_ru:
                index = pending_ru.pop()
                if index in component_ru:
                    continue
                component_ru.add(index)
                values = en_ru[index].get("left", [])
                new_english = {value for value in values if isinstance(value, int)} - english
                english.update(new_english)
                for other, row in enumerate(zh_en):
                    right_values = row.get("right", [])
                    if any(value in english for value in right_values if isinstance(value, int)) and other not in component_zh:
                        pending_zh.append(other)
        seen_zh.update(component_zh)
        units.append({
            "zh": sorted({value for index in component_zh for value in zh_en[index].get("left", []) if isinstance(value, int)}),
            "en": sorted(english),
            "ru": sorted({value for index in component_ru for value in en_ru[index].get("right", []) if isinstance(value, int)}),
        })
    return units


def _bertalign_bridge_units(zh_en_path: Path, en_ru_path: Path) -> list[dict[str, list[int]]]:
    """Join two Bertalign outputs through English, retaining explicit extras."""
    zh_en = json.loads(zh_en_path.read_text(encoding="utf-8")).get("alignments", [])
    en_ru = json.loads(en_ru_path.read_text(encoding="utf-8")).get("alignments", [])
    if not isinstance(zh_en, list) or not isinstance(en_ru, list):
        raise ValueError("Invalid Bertalign JSON")
    z_links = [{value for value in row.get("right", []) if isinstance(value, int)} for row in zh_en]
    r_links = [{value for value in row.get("left", []) if isinstance(value, int)} for row in en_ru]
    z_for_en: dict[int, set[int]] = {}
    r_for_en: dict[int, set[int]] = {}
    for index, values in enumerate(z_links):
        for value in values:
            z_for_en.setdefault(value, set()).add(index)
    for index, values in enumerate(r_links):
        for value in values:
            r_for_en.setdefault(value, set()).add(index)

    components: list[tuple[set[int], set[int], set[int]]] = []
    seen_z: set[int] = set()
    seen_r: set[int] = set()
    for start in range(len(zh_en)):
        if start in seen_z:
            continue
        pending_z, pending_r = [start], []
        component_z: set[int] = set()
        component_r: set[int] = set()
        english: set[int] = set()
        while pending_z or pending_r:
            while pending_z:
                index = pending_z.pop()
                if index in component_z:
                    continue
                component_z.add(index)
                english.update(z_links[index])
                for value in z_links[index]:
                    pending_r.extend(r_for_en.get(value, set()) - component_r)
            while pending_r:
                index = pending_r.pop()
                if index in component_r:
                    continue
                component_r.add(index)
                english.update(r_links[index])
                for value in r_links[index]:
                    pending_z.extend(z_for_en.get(value, set()) - component_z)
        seen_z.update(component_z)
        seen_r.update(component_r)
        components.append((component_z, component_r, english))
    # Bertalign uses empty left lists for target-only fan additions. Preserve
    # them as independent audit units rather than silently dropping them.
    components.extend((set(), {index}, set()) for index in range(len(en_ru)) if index not in seen_r)

    def unit(component: tuple[set[int], set[int], set[int]]) -> dict[str, list[int]]:
        z_indexes, r_indexes, english = component
        return {
            "zh": sorted({value for index in z_indexes for value in zh_en[index].get("left", []) if isinstance(value, int)}),
            "en": sorted(english),
            "ru": sorted({value for index in r_indexes for value in en_ru[index].get("right", []) if isinstance(value, int)}),
        }

    return [unit(component) for component in components]


def audit_bertalign_fan_translation(
    zh_dir: Path, en_dir: Path, ru_dir: Path, zh_en_dir: Path, en_ru_dir: Path, output: Path, model: str, chapter_count: int = 3
) -> int:
    groups = (_files(zh_dir), _files(en_dir), _files(ru_dir))
    count = min(min(len(group) for group in groups), chapter_count)
    if count < 1:
        raise ValueError("No matching chapter files found")
    output.mkdir(parents=True, exist_ok=True)
    for chapter_index in range(count):
        chapter = chapter_index + 1
        zh, en, ru = (_paragraphs(group[chapter_index]) for group in groups)
        zh_en_path = zh_en_dir / f"ch_{chapter:04d}_zh_en.json"
        en_ru_path = en_ru_dir / f"ch_{chapter:04d}_en_ru.json"
        if not zh_en_path.exists() or not en_ru_path.exists():
            raise ValueError(f"Missing Bertalign pair alignment for chapter {chapter}")
        units = _bertalign_bridge_units(zh_en_path, en_ru_path)
        windows = []
        for offset in range(0, len(units), 5):
            current = []
            for unit_number, unit in enumerate(units[offset : offset + 5], offset + 1):
                current.append({"unit": unit_number, **unit, "zh_text": _joined(zh, unit["zh"]), "en_text": _joined(en, unit["en"]), "ru_text": _joined(ru, unit["ru"])})
            total_windows = (len(units) + 4) // 5
            (output / "progress.json").write_text(json.dumps({"status": "running", "chapter": chapter, "window": len(windows) + 1, "total_windows": total_windows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            rendered = "\n\n".join(f"UNIT {item['unit']} (zh {item['zh']}; en {item['en']}; ru {item['ru']})\nZH: {item['zh_text']}\nEN: {item['en_text']}\nRU: {item['ru_text']}" for item in current)
            prompt = (
                "You audit a Russian fan translation against Chinese original and official English. "
                "Units were fixed by Bertalign; NEVER infer or change paragraph alignment. Empty [] is explicit evidence: RU without ZH/EN may be an addition; source without RU may be an omission. "
                "Return JSON only as {\"findings\":[...]}. Each finding must contain unit, type (addition|omission|reordering|meaning_shift), severity (low|medium|high), and concise Russian reason. "
                "Be conservative and report only checkable differences. Do not flag normal literary rewording. If none, return {\"findings\":[]}.\n\n" + rendered
            )
            try:
                result = json.loads(chat(prompt, model=model, temperature=0.0, context=4096, timeout=120, json_mode=True))
                findings = result.get("findings", []) if isinstance(result, dict) else []
            except (TimeoutError, URLError, json.JSONDecodeError) as error:
                findings = [{"type": "review_required", "severity": "high", "reason": f"Qwen audit request failed: {error}"}]
            windows.append({"units": current, "findings": findings})
        report = {"chapter": chapter, "status": "qwen_assisted_needs_human_review", "alignment_method": "bertalign_labse_english_bridge", "windows": windows}
        (output / f"ch_{chapter:04d}_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "progress.json").write_text(json.dumps({"status": "complete", "chapters": count}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return count


def audit_embedding_aligned_fan_translation(
    zh_dir: Path, en_dir: Path, ru_dir: Path, zh_en_dir: Path, en_ru_dir: Path, output: Path, model: str, chapter_count: int = 3
) -> int:
    groups = (_files(zh_dir), _files(en_dir), _files(ru_dir))
    count = min(min(len(group) for group in groups), chapter_count)
    if count < 1:
        raise ValueError("No matching chapter files found")
    output.mkdir(parents=True, exist_ok=True)
    for chapter_index in range(count):
        chapter = chapter_index + 1
        zh, en, ru = (_paragraphs(group[chapter_index]) for group in groups)
        zh_en_path = zh_en_dir / f"ch_{chapter:04d}_zh_en.json"
        en_ru_path = en_ru_dir / f"ch_{chapter:04d}_en_ru.json"
        if not zh_en_path.exists() or not en_ru_path.exists():
            raise ValueError(f"Missing BGE pair alignment for chapter {chapter}")
        units = _embedding_units(zh_en_path, en_ru_path)
        windows = []
        for offset in range(0, len(units), 5):
            window_units = []
            for unit_number, unit in enumerate(units[offset : offset + 5], offset + 1):
                window_units.append({
                    "unit": unit_number, **unit,
                    "zh_text": _joined(zh, unit["zh"]), "en_text": _joined(en, unit["en"]), "ru_text": _joined(ru, unit["ru"]),
                })
            total_windows = (len(units) + 4) // 5
            (output / "progress.json").write_text(json.dumps({"status": "running", "chapter": chapter, "window": len(windows) + 1, "total_windows": total_windows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            rendered = "\n\n".join(
                f"UNIT {item['unit']} (zh {item['zh']}; en {item['en']}; ru {item['ru']})\nZH: {item['zh_text']}\nEN: {item['en_text']}\nRU: {item['ru_text']}"
                for item in window_units
            )
            prompt = (
                "You audit a Russian fan translation against Chinese original and official English. "
                "The units below were aligned by multilingual BGE-M3 embeddings through the English bridge; do not align by paragraph number. "
                "Return JSON only as {\"findings\":[...]}. Each finding must contain unit, type "
                "(addition|omission|reordering|meaning_shift), severity (low|medium|high), and a concise Russian reason. "
                "Be conservative: report only a checkable difference in meaning or order. Do not flag normal literary rewording. "
                "If none, return {\"findings\":[]}.\n\n" + rendered
            )
            try:
                result = json.loads(chat(prompt, model=model, temperature=0.0, context=4096, timeout=120, json_mode=True))
                findings = result.get("findings", []) if isinstance(result, dict) else []
            except (TimeoutError, URLError, json.JSONDecodeError) as error:
                findings = [{"type": "review_required", "severity": "high", "reason": f"Qwen audit request failed: {error}"}]
            windows.append({"units": window_units, "findings": findings})
        report = {"chapter": chapter, "status": "qwen_assisted_needs_human_review", "alignment_method": "bge_m3_english_bridge", "windows": windows}
        (output / f"ch_{chapter:04d}_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "progress.json").write_text(json.dumps({"status": "complete", "chapters": count}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return count


def audit_aligned_fan_translation(
    zh_dir: Path,
    en_dir: Path,
    ru_dir: Path,
    alignment_first5: Path,
    alignment_rest: Path,
    output: Path,
    model: str,
    chapter_count: int = 3,
) -> int:
    groups = (_files(zh_dir), _files(en_dir), _files(ru_dir))
    count = min(min(len(group) for group in groups), chapter_count)
    if count < 1:
        raise ValueError("No matching chapter files found")
    output.mkdir(parents=True, exist_ok=True)
    for chapter_index in range(count):
        chapter = chapter_index + 1
        alignment_dir = alignment_first5 if chapter <= 5 else alignment_rest
        alignment_path = alignment_dir / f"ch_{chapter:04d}_alignment.json"
        if not alignment_path.exists():
            raise ValueError(f"Missing alignment file: {alignment_path}")
        zh, en, ru = (_paragraphs(group[chapter_index]) for group in groups)
        alignment = json.loads(alignment_path.read_text(encoding="utf-8"))
        report_windows = []
        for window_number, window in enumerate(alignment.get("windows", []), 1):
            rows = _normalise_rows(window.get("alignment"))
            units = []
            for unit_number, row in enumerate(rows, 1):
                units.append({
                    "unit": unit_number,
                    "zh": row.get("zh", []),
                    "en": row.get("en", []),
                    "ru": row.get("ru", []),
                    "zh_text": _joined(zh, row.get("zh")),
                    "en_text": _joined(en, row.get("en")),
                    "ru_text": _joined(ru, row.get("ru")),
                })
            (output / "progress.json").write_text(
                json.dumps({"status": "running", "chapter": chapter, "window": window_number, "total_windows": len(alignment.get("windows", []))}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            rendered = "\n\n".join(
                f"UNIT {item['unit']} (zh {item['zh']}; en {item['en']}; ru {item['ru']})\n"
                f"ZH: {item['zh_text']}\nEN: {item['en_text']}\nRU: {item['ru_text']}"
                for item in units
            )
            prompt = (
                "You audit a Russian fan translation against Chinese original and official English. "
                "The units below are already aligned; do not infer alignment from paragraph numbers. "
                "Return JSON only as {\"findings\":[...]}. Each finding must contain unit, type "
                "(addition|omission|reordering|meaning_shift), severity (low|medium|high), and a concise Russian reason. "
                "Be conservative: report only a checkable difference in meaning or order. Do not flag normal literary rewording. "
                "If none, return {\"findings\":[]}.\n\n" + rendered
            )
            try:
                result = json.loads(chat(prompt, model=model, temperature=0.0, context=4096, timeout=120, json_mode=True))
                findings = result.get("findings", []) if isinstance(result, dict) else []
            except (TimeoutError, URLError, json.JSONDecodeError) as error:
                findings = [{"type": "review_required", "severity": "high", "reason": f"Qwen audit request failed: {error}"}]
            report_windows.append({"alignment_offset": window.get("offset"), "units": units, "findings": findings})
        report = {"chapter": chapter, "status": "qwen_assisted_needs_human_review", "alignment_source": str(alignment_path), "windows": report_windows}
        (output / f"ch_{chapter:04d}_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "progress.json").write_text(json.dumps({"status": "complete", "chapters": count}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return count


def audit_fan_translation(zh_dir: Path, en_dir: Path, ru_dir: Path, output: Path, model: str) -> int:
    groups = (_files(zh_dir), _files(en_dir), _files(ru_dir))
    count = min(len(group) for group in groups)
    if count < 1:
        raise ValueError("No matching chapter files found")
    output.mkdir(parents=True, exist_ok=True)
    for chapter_index in range(count):
        zh, en, ru = (_paragraphs(group[chapter_index]) for group in groups)
        windows = []
        for offset in range(0, max(len(zh), len(en), len(ru)), 5):
            window_number = len(windows) + 1
            total_windows = (max(len(zh), len(en), len(ru)) + 4) // 5
            (output / "progress.json").write_text(
                json.dumps({"status": "running", "chapter": chapter_index + 1, "window": window_number, "total_windows": total_windows}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            prompt = (
                "You audit a Russian fan translation against Chinese original and official English. "
                "Return JSON only as {\"findings\":[...]}. Each finding must have zh, en, ru arrays of paragraph numbers, "
                "type (addition|omission|reordering|meaning_shift), severity (low|medium|high), and reason in Russian. "
                "Report only checkable differences in meaning or order. Ignore punctuation and normal stylistic rewording. "
                "If no finding exists, return {\"findings\":[]}.\n\n"
                f"Chinese:\n{_numbered(zh[offset:offset + 5], offset + 1)}\n\n"
                f"Official English:\n{_numbered(en[offset:offset + 5], offset + 1)}\n\n"
                f"Russian fan translation:\n{_numbered(ru[offset:offset + 5], offset + 1)}"
            )
            try:
                result = json.loads(chat(prompt, model=model, temperature=0.0, context=4096, timeout=120, json_mode=True))
                findings = result.get("findings", []) if isinstance(result, dict) else []
            except (TimeoutError, URLError, json.JSONDecodeError) as error:
                findings = [{"type": "review_required", "severity": "high", "reason": f"Qwen audit request failed: {error}"}]
            windows.append({"offset": offset + 1, "findings": findings})
        report = {"chapter": chapter_index + 1, "status": "qwen_assisted_needs_human_review", "windows": windows}
        (output / f"ch_{chapter_index + 1:04d}_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (output / "progress.json").write_text(json.dumps({"status": "chapter_complete", "chapter": chapter_index + 1, "windows": len(windows)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "progress.json").write_text(json.dumps({"status": "complete", "chapters": count}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return count
