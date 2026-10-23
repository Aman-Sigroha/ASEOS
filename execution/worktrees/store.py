"""Persistent worktree registry and the cross-process repository lock.

Layout, inside the repository's *common* git directory (so every linked
worktree shares it, `git status` never sees it, and it is not tracked):

    <common>/aseos/lock                    lock file (O_EXCL)
    <common>/aseos/worktrees/<id>.json     one record per worktree
    <common>/aseos/removed/<id>.patch      backup of discarded changes
"""

import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from execution.git.repo import GitError
from execution.patches.fileio import atomic_write_bytes
from execution.worktrees.record import WorktreeRecord

LOCK_TIMEOUT_SECONDS = 30.0
LOCK_STALE_SECONDS = 300.0

_GUARD = threading.Lock()


class _LockState:
    def __init__(self) -> None:
        self.rlock = threading.RLock()
        self.depth = 0


_LOCKS: dict[str, _LockState] = {}


def _lock_busy() -> GitError:
    return GitError(
        "GIT_FAILURE",
        "The worktree registry is locked by another operation; retry later.",
        {"lock_conflict": True},
    )


def _acquire_file(path: Path, timeout: float, stale_after: float) -> None:
    deadline = time.monotonic() + timeout
    path.parent.mkdir(parents=True, exist_ok=True)

    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except FileNotFoundError:
                continue

            if age > stale_after:
                # A holder that died mid-operation: break its lock.
                path.unlink(missing_ok=True)
                continue

            if time.monotonic() > deadline:
                raise _lock_busy()

            time.sleep(0.05)
            continue

        with os.fdopen(fd, "w") as handle:
            handle.write(f"{os.getpid()} {time.time()}\n")

        return


@contextmanager
def repo_lock(
    path: Path,
    timeout: float = LOCK_TIMEOUT_SECONDS,
    stale_after: float = LOCK_STALE_SECONDS,
) -> Iterator[None]:
    """Reentrant in-process lock plus an exclusive lock file across processes."""

    key = os.path.normcase(str(path))

    with _GUARD:
        state = _LOCKS.setdefault(key, _LockState())

    if not state.rlock.acquire(timeout=timeout):
        raise _lock_busy()

    try:
        if state.depth == 0:
            _acquire_file(path, timeout, stale_after)

        state.depth += 1

        try:
            yield
        finally:
            state.depth -= 1

            if state.depth == 0:
                path.unlink(missing_ok=True)
    finally:
        state.rlock.release()


class WorktreeStore:
    def __init__(self, base: Path) -> None:
        self.base = base
        self.records_dir = base / "worktrees"
        self.backups_dir = base / "removed"
        self.lock_path = base / "lock"

    def lock(self):
        return repo_lock(self.lock_path)

    def _file(self, worktree_id: str) -> Path:
        if not worktree_id.replace("-", "").isalnum():
            raise GitError("INVALID_ARGUMENTS", f"Invalid worktree id: {worktree_id!r}")

        return self.records_dir / f"{worktree_id}.json"

    def save(self, record: WorktreeRecord) -> None:
        self.records_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(
            self._file(record.worktree_id),
            record.model_dump_json(indent=2).encode("utf-8"),
        )

    def load(self, worktree_id: str) -> WorktreeRecord | None:
        path = self._file(worktree_id)

        if not path.is_file():
            return None

        return self._parse(path)

    @staticmethod
    def _parse(path: Path) -> WorktreeRecord:
        try:
            return WorktreeRecord.model_validate_json(path.read_bytes())
        except (ValidationError, ValueError, OSError) as exc:
            raise GitError(
                "INTERNAL_ERROR",
                f"Worktree record {path.name} is unreadable: {exc}",
                {"record_file": path.name},
            ) from exc

    def list(self) -> tuple[list[WorktreeRecord], list[str]]:
        """Return (records, names of unreadable record files)."""

        records: list[WorktreeRecord] = []
        corrupt: list[str] = []

        if self.records_dir.is_dir():
            for path in sorted(self.records_dir.glob("*.json")):
                try:
                    records.append(self._parse(path))
                except GitError:
                    corrupt.append(path.name)

        return records, corrupt

    def delete(self, worktree_id: str) -> None:
        self._file(worktree_id).unlink(missing_ok=True)
