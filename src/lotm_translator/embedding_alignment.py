from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def _paragraphs(path: Path) -> list[str]:
    return [item.strip() for item in path.read_text(encoding="utf-8").split("\n\n") if item.strip() and not item.startswith("# ")]


def _block_score(left: np.ndarray, right: np.ndarray, i: int, j: int, a: int, b: int) -> float:
    left_vector = left[i:i + a].mean(axis=0)
    right_vector = right[j:j + b].mean(axis=0)
    score = float(np.dot(left_vector, right_vector) / (np.linalg.norm(left_vector) * np.linalg.norm(right_vector)))
    return score - 0.025 * (a + b - 2)


def _encode(texts: list[str], model_path: Path, device: str) -> np.ndarray:
    if device == "cpu":
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(str(model_path), device="cpu")
        return model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    if device != "dml":
        raise ValueError("device must be 'cpu' or 'dml'")

    # Windows DirectML is tied to an older PyTorch stack. Loading BGE-M3 via
    # Transformers keeps the main CPU environment independent of that stack.
    import torch
    import torch_directml
    from transformers import AutoModel, AutoTokenizer

    dml = torch_directml.device()
    tokenizer = AutoTokenizer.from_pretrained(str(model_path))
    model = AutoModel.from_pretrained(str(model_path)).to(dml).eval()
    batches: list[np.ndarray] = []
    with torch.no_grad():
        for offset in range(0, len(texts), 8):
            encoded = tokenizer(texts[offset : offset + 8], padding=True, truncation=True, max_length=8192, return_tensors="pt")
            encoded = {name: value.to(dml) for name, value in encoded.items()}
            vectors = model(**encoded).last_hidden_state[:, 0]  # BGE-M3 uses CLS pooling.
            vectors = torch.nn.functional.normalize(vectors, p=2, dim=1)
            batches.append(vectors.cpu().numpy())
    return np.concatenate(batches, axis=0)


def align_pair(
    left_path: Path,
    right_path: Path,
    output: Path,
    model_path: Path,
    max_group: int = 3,
    device: str = "cpu",
) -> dict[str, object]:
    left_text, right_text = _paragraphs(left_path), _paragraphs(right_path)
    left_vectors = _encode(left_text, model_path, device)
    right_vectors = _encode(right_text, model_path, device)
    rows, columns = len(left_text), len(right_text)
    dp = np.full((rows + 1, columns + 1), -np.inf, dtype=np.float64)
    back: dict[tuple[int, int], tuple[int, int, float]] = {}
    dp[0, 0] = 0.0
    for i in range(rows + 1):
        for j in range(columns + 1):
            if not np.isfinite(dp[i, j]):
                continue
            for a in range(1, min(max_group, rows - i) + 1):
                for b in range(1, min(max_group, columns - j) + 1):
                    score = _block_score(left_vectors, right_vectors, i, j, a, b)
                    if dp[i, j] + score > dp[i + a, j + b]:
                        dp[i + a, j + b] = dp[i, j] + score
                        back[(i + a, j + b)] = (a, b, score)
    records = []
    i, j = rows, columns
    while i or j:
        a, b, score = back[(i, j)]
        records.append({"left": list(range(i - a + 1, i + 1)), "right": list(range(j - b + 1, j + 1)), "similarity": round(score + 0.025 * (a + b - 2), 4)})
        i, j = i - a, j - b
    records.reverse()
    report = {
        "method": "bge_m3_dense_embeddings_dynamic_programming",
        "device": device,
        "left_file": left_path.name,
        "right_file": right_path.name,
        "left_paragraphs": rows,
        "right_paragraphs": columns,
        "max_group": max_group,
        "coverage": {"left": sum(len(item["left"]) for item in records), "right": sum(len(item["right"]) for item in records)},
        "alignments": records,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def validate_pair(
    alignment_path: Path,
    left_path: Path,
    right_path: Path,
    output: Path,
    minimum_similarity: float = 0.65,
) -> dict[str, object]:
    """Stop at the first weak BGE group instead of allowing alignment drift."""
    alignment = json.loads(alignment_path.read_text(encoding="utf-8"))
    rows = alignment.get("alignments", [])
    if not isinstance(rows, list):
        raise ValueError(f"Invalid alignment file: {alignment_path}")
    left_text, right_text = _paragraphs(left_path), _paragraphs(right_path)
    first_suspect = next((index for index, row in enumerate(rows) if float(row.get("similarity", 0.0)) < minimum_similarity), None)
    report: dict[str, object] = {
        "alignment": str(alignment_path),
        "minimum_similarity": minimum_similarity,
        "status": "passed" if first_suspect is None else "review_required",
    }
    if first_suspect is not None:
        start, end = max(0, first_suspect - 2), min(len(rows), first_suspect + 3)
        context = []
        for index in range(start, end):
            row = rows[index]
            left_numbers = [value for value in row.get("left", []) if isinstance(value, int)]
            right_numbers = [value for value in row.get("right", []) if isinstance(value, int)]
            context.append({
                "group": index + 1,
                "left": left_numbers,
                "right": right_numbers,
                "similarity": row.get("similarity"),
                "left_text": "\n\n".join(left_text[value - 1] for value in left_numbers if 1 <= value <= len(left_text)),
                "right_text": "\n\n".join(right_text[value - 1] for value in right_numbers if 1 <= value <= len(right_text)),
            })
        report["first_suspect_group"] = first_suspect + 1
        report["context"] = context
        report["instruction"] = "Do not continue automatic alignment after this group. Review this boundary before auditing translation."
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def review_boundary(validation_path: Path, output: Path, model: str) -> dict[str, object]:
    """Ask Qwen to judge one BGE boundary; it never changes the alignment itself."""
    from .ollama import chat

    report = json.loads(validation_path.read_text(encoding="utf-8"))
    context = report.get("context", [])
    if report.get("status") != "review_required" or not isinstance(context, list):
        result = {"status": "not_needed", "validation": str(validation_path)}
    else:
        rendered = "\n\n".join(
            f"GROUP {row.get('group')} | left {row.get('left')} ↔ right {row.get('right')} | similarity {row.get('similarity')}\n"
            f"LEFT: {row.get('left_text')}\nRIGHT: {row.get('right_text')}"
            for row in context
        )
        prompt = (
            "You verify ONE paragraph-alignment boundary for a multilingual literary corpus. "
            "Do not audit translation quality. Check whether the displayed neighboring left/right groups preserve order and correspond in meaning. "
            "Return JSON only: {\"status\":\"confirmed|correction_needed|uncertain\",\"reason_ru\":\"...\"}. "
            "Use correction_needed only when there is clear cross-group drift, not merely different paragraph splitting.\n\n" + rendered
        )
        try:
            answer = json.loads(chat(prompt, model=model, temperature=0.0, context=4096, timeout=120, json_mode=True))
            result = {"validation": str(validation_path), "boundary_group": report.get("first_suspect_group"), **(answer if isinstance(answer, dict) else {"status": "uncertain"})}
        except Exception as error:  # Boundary review must remain a non-destructive gate.
            result = {"validation": str(validation_path), "boundary_group": report.get("first_suspect_group"), "status": "uncertain", "reason_ru": f"Qwen request failed: {error}"}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
