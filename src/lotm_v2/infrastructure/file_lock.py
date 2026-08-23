"""Cross-process advisory locks for Pairwise local state and Gold transitions."""
from __future__ import annotations

from contextlib import AbstractContextManager
import os
from pathlib import Path
import time
from typing import BinaryIO


class FileLockTimeout(TimeoutError):
    pass


class ExclusiveFileLock(AbstractContextManager["ExclusiveFileLock"]):
    """Own one OS-level byte/file lock; the lock file may safely survive crashes."""

    def __init__(self, path: Path, *, timeout: float = 10.0) -> None:
        self.path = path
        self.timeout = timeout
        self._stream: BinaryIO | None = None

    def __enter__(self) -> "ExclusiveFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
            os.fsync(stream.fileno())
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._acquire(stream)
                self._stream = stream
                return self
            except OSError as error:
                if time.monotonic() >= deadline:
                    stream.close()
                    raise FileLockTimeout(f"Timed out acquiring resource lock: {self.path}") from error
                time.sleep(0.01)

    @staticmethod
    def _acquire(stream: BinaryIO) -> None:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _release(stream: BinaryIO) -> None:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                self._release(stream)
            finally:
                stream.close()
