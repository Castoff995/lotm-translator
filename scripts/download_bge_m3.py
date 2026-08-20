from pathlib import Path

from sentence_transformers import SentenceTransformer


target = Path("models/embeddings/bge-m3")
target.parent.mkdir(parents=True, exist_ok=True)
model = SentenceTransformer("BAAI/bge-m3")
model.save(str(target))
print("BGE-M3 download and save complete")
