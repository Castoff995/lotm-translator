"""Small, repeatable CPU vs DirectML benchmark for the local BGE-M3 model."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import torch


def paragraphs(path: Path) -> list[str]:
    return [part.strip() for part in path.read_text(encoding="utf-8").split("\n\n") if part.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", choices=("cpu", "dml"), required=True)
    parser.add_argument("--model", default="models/embeddings/bge-m3")
    parser.add_argument("--input", default="data/raw/qwen_10/en/0005_Chapter 1_ Crimson.txt")
    parser.add_argument("--raw-transformers", action="store_true")
    args = parser.parse_args()

    device = args.device
    if device == "dml":
        import torch_directml
        device = str(torch_directml.device())

    texts = paragraphs(Path(args.input))
    started = time.perf_counter()
    if args.raw_transformers:
        from transformers import AutoModel, AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(args.model)
        model = AutoModel.from_pretrained(args.model).to(device).eval()
    else:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(args.model, device=device)
    load_seconds = time.perf_counter() - started

    started = time.perf_counter()
    if args.raw_transformers:
        batches = []
        # DirectML currently cannot run this XLM-RoBERTa path under inference_mode.
        # no_grad keeps the benchmark inference-only while remaining compatible.
        with torch.no_grad():
            for offset in range(0, len(texts), 8):
                encoded = tokenizer(texts[offset : offset + 8], padding=True, truncation=True, max_length=8192, return_tensors="pt")
                encoded = {name: tensor.to(device) for name, tensor in encoded.items()}
                # BGE-M3's saved SentenceTransformer pipeline uses CLS pooling.
                vector = model(**encoded).last_hidden_state[:, 0]
                batches.append(torch.nn.functional.normalize(vector, p=2, dim=1).cpu())
        embeddings = torch.cat(batches)
        actual_device = str(next(model.parameters()).device)
    else:
        embeddings = model.encode(texts, batch_size=8, normalize_embeddings=True, show_progress_bar=False)
        actual_device = str(model.device)
    encode_seconds = time.perf_counter() - started
    print({
        "requested_device": args.device,
        "actual_device": actual_device,
        "paragraphs": len(texts),
        "dimensions": int(embeddings.shape[1]),
        "load_seconds": round(load_seconds, 2),
        "encode_seconds": round(encode_seconds, 2),
        "paragraphs_per_second": round(len(texts) / encode_seconds, 2),
    })


if __name__ == "__main__":
    main()
