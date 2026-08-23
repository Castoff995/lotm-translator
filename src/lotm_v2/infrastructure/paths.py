"""Central v2 data layout policy."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath

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

    def chapter_map(self, source: SourceId) -> Path:
        return self.data / "manifests" / "v2" / "chapter-maps" / f"{source}.json"

    def normalized_chapter(self, source: SourceId, chapter: int) -> Path:
        return self.data / "normalized" / "v2" / str(source) / f"ch_{chapter:04d}.json"

    def gold_chapter(self, chapter: int) -> Path:
        return self.data / "gold" / "v2" / f"ch_{chapter:04d}.json"

    def pairwise_gold_chapter(self, left: SourceId, right: SourceId, chapter: int) -> Path:
        return self.data / "gold" / "v2" / "pairwise" / f"{left}--{right}" / f"ch_{chapter:04d}.json"

    def review_sessions(self) -> Path:
        return self.data / "review_sessions" / "v2"

    def active_review_session(self, work_id: str, chapter: int) -> Path:
        return self.review_sessions() / work_id / f"ch_{chapter:04d}.active.json"

    def archived_review_session(self, work_id: str, chapter: int, session_id: str, status: str) -> Path:
        return self.review_sessions() / work_id / "archive" / f"ch_{chapter:04d}.{session_id}.{status}.json"

    def pairwise_review_sessions(self) -> Path:
        return self.review_sessions() / "pairwise"

    def pairwise_locks(self) -> Path:
        return self.pairwise_review_sessions() / ".locks"

    def pairwise_resource_lock(self, kind: str, resource: Path) -> Path:
        if kind not in {"session", "gold"}:
            raise ValueError(f"Unsupported Pairwise lock kind: {kind}")
        identity = os.path.normcase(str(resource.resolve())).encode("utf-8")
        digest = hashlib.sha256(identity).hexdigest()
        return self.pairwise_locks() / f"{kind}-{digest}.lock"

    def active_pairwise_review_session(
        self, work_id: str, left: SourceId, right: SourceId, chapter: int,
    ) -> Path:
        return (
            self.pairwise_review_sessions() / work_id / f"{left}--{right}"
            / f"ch_{chapter:04d}.active.json"
        )

    def archived_pairwise_review_session(
        self, work_id: str, left: SourceId, right: SourceId, chapter: int,
        session_id: str, status: str,
    ) -> Path:
        return (
            self.pairwise_review_sessions() / work_id / f"{left}--{right}" / "archive"
            / f"ch_{chapter:04d}.{session_id}.{status}.json"
        )

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root.resolve()).as_posix()

    def resolve(self, stored_path: str) -> Path:
        candidate = Path(stored_path)
        return candidate if candidate.is_absolute() else self.root.resolve() / candidate

    def resolve_repository_relative(self, stored_path: str) -> Path:
        """Resolve one canonical POSIX-style repository-relative stored path."""
        if not isinstance(stored_path, str) or not stored_path or "\\" in stored_path:
            raise ValueError("Stored path must be a canonical repository-relative POSIX path")
        windows = PureWindowsPath(stored_path)
        posix = PurePosixPath(stored_path)
        if windows.is_absolute() or windows.drive or windows.root or posix.is_absolute():
            raise ValueError(f"Stored path must not be absolute or drive-relative: {stored_path}")
        if any(part in {"", ".", ".."} for part in stored_path.split("/")):
            raise ValueError(f"Stored path is not canonical: {stored_path}")
        if posix.as_posix() != stored_path:
            raise ValueError(f"Stored path is not canonical: {stored_path}")
        root = self.root.resolve()
        resolved = (root / Path(*posix.parts)).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise ValueError(f"Stored path escapes project root: {stored_path}") from error
        return resolved

    def require_normalized_chapter_path(
        self, stored_path: str, source: SourceId, chapter: int,
    ) -> Path:
        resolved = self.resolve_repository_relative(stored_path)
        expected = self.normalized_chapter(source, chapter).resolve()
        if os.path.normcase(str(resolved)) != os.path.normcase(str(expected)):
            raise ValueError(
                f"Normalized source path is not canonical for {source} chapter {chapter}: {stored_path}"
            )
        return resolved

    def require_pairwise_gold_path(
        self, path: Path, left: SourceId, right: SourceId, chapter: int,
    ) -> Path:
        root = self.root.resolve()
        resolved = path.resolve()
        expected = self.pairwise_gold_chapter(left, right, chapter).resolve()
        try:
            resolved.relative_to(root)
            expected.relative_to(root)
        except ValueError as error:
            raise ValueError("Pairwise Gold target escapes project root") from error
        if os.path.normcase(str(resolved)) != os.path.normcase(str(expected)):
            raise ValueError(f"Pairwise Gold target must use canonical path: {expected}")
        return resolved
