"""Central v2 data layout policy."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..domain import SourceId


@dataclass(frozen=True)
class PathPolicy:
    root: Path

    @property
    def data(self) -> Path:
        return self.root.resolve() / "data"

    def manifest(self, source: SourceId) -> Path:
        return self.data / "manifests" / "v2" / f"{source}.json"

    def raw_chapter(self, source: SourceId, chapter: int) -> Path:
        return self.data / "raw" / "v2" / str(source) / f"ch_{chapter:04d}.txt"

    def raw_epub(self, source: SourceId) -> Path:
        return self.data / "raw" / "v2" / str(source) / "source.epub"

    def epub_artifact(self, source: SourceId, chapter: int) -> Path:
        return self.data / "manifests" / "v2" / "paragraphization" / str(source) / f"ch_{chapter:04d}_epub.json"

    def normalized_chapter(self, source: SourceId, chapter: int) -> Path:
        return self.data / "normalized" / "v2" / str(source) / f"ch_{chapter:04d}.json"

    def gold_chapter(self, chapter: int) -> Path:
        return self.data / "gold" / "v2" / f"ch_{chapter:04d}.json"

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root.resolve()).as_posix()

    def resolve(self, stored_path: str) -> Path:
        candidate = Path(stored_path)
        return candidate if candidate.is_absolute() else self.root.resolve() / candidate
