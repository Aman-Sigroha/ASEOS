"""Identify the exact state of source code that a result (e.g. a test run)
was produced from.

The fingerprint covers file *contents* (committed, modified and untracked
files that are not git-ignored), so a result computed before an edit does not
match the workspace after it. Generated clutter (bytecode, caches,
dependency folders, our own report files) is excluded, otherwise running the
tests would change the fingerprint of the code that was tested.
"""

import hashlib
import os
from pathlib import Path
from typing import Any

from execution.git.repo import GitError, ensure_repo_root, head_sha, run_git

EXCLUDED_DIRS = frozenset(
    {
        "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
        ".tox", ".nox", ".venv", "venv", "node_modules", ".gradle",
        ".aseos_worktrees", ".git", ".idea", ".vscode",
    }
)
EXCLUDED_SUFFIXES = (".pyc", ".pyo")
EXCLUDED_NAMES = frozenset({".coverage"})
EXCLUDED_PREFIXES = (".aseos_report_",)
MAX_HASHED_FILE_BYTES = 50 * 1024 * 1024
MAX_FILES = 200_000


def is_excluded(relative: str) -> bool:
    parts = relative.replace("\\", "/").split("/")
    name = parts[-1]

    return (
        any(part in EXCLUDED_DIRS for part in parts[:-1])
        or name in EXCLUDED_DIRS
        or name in EXCLUDED_NAMES
        or name.endswith(EXCLUDED_SUFFIXES)
        or name.startswith(EXCLUDED_PREFIXES)
    )


def _hash_entry(path: Path) -> str:
    try:
        if path.is_symlink():
            return "link:" + hashlib.sha256(os.readlink(path).encode()).hexdigest()

        if not path.is_file():
            return "missing"

        size = path.stat().st_size

        if size > MAX_HASHED_FILE_BYTES:
            return f"large:{size}"

        digest = hashlib.sha256()

        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)

        return digest.hexdigest()
    except OSError:
        return "unreadable"


def _git_files(workspace: Path) -> list[str] | None:
    try:
        ensure_repo_root(workspace)
        out = run_git(
            workspace,
            ["ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            timeout=60,
        ).stdout
    except GitError:
        return None

    return sorted({p for p in out.split("\0") if p})


def _walk_files(workspace: Path) -> list[str]:
    found: list[str] = []

    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]

        for name in files:
            found.append(
                os.path.relpath(os.path.join(root, name), workspace).replace("\\", "/")
            )

            if len(found) >= MAX_FILES:
                return sorted(found)

    return sorted(found)


def _dir_is_inside(folder: Path, real_workspace: Path, cache: dict[Path, bool]) -> bool:
    """Whether `folder` really lives under the workspace (not through a junction or
    symlink). Checked once per folder: resolving every file costs milliseconds each
    on Windows."""

    known = cache.get(folder)

    if known is None:
        known = Path(os.path.realpath(folder)).is_relative_to(real_workspace)
        cache[folder] = known

    return known


def workspace_version(workspace: Path) -> dict[str, Any]:
    """`{kind, head, fingerprint, file_count}` for the workspace's current state."""

    workspace = Path(workspace).resolve()
    listed = _git_files(workspace)
    kind = "git" if listed is not None else "files"
    names = listed if listed is not None else _walk_files(workspace)
    digest = hashlib.sha256()
    count = 0
    real_workspace = Path(os.path.realpath(workspace))
    inside_cache: dict[Path, bool] = {}

    for name in names:
        if is_excluded(name):
            continue

        entry = workspace / name

        # A junction/symlinked folder can make files elsewhere on the machine
        # look like part of the workspace; never read (or depend on) those.
        if not _dir_is_inside(entry.parent, real_workspace, inside_cache):
            digest.update(f"{name}\0outside\n".encode("utf-8"))
            count += 1
            continue

        digest.update(f"{name}\0{_hash_entry(entry)}\n".encode("utf-8"))
        count += 1

    head = None

    if kind == "git":
        try:
            head = head_sha(workspace)
        except GitError:
            head = None

    return {
        "kind": kind,
        "head": head,
        "fingerprint": digest.hexdigest(),
        "file_count": count,
    }
