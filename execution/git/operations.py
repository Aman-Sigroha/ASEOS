"""Simple Git helpers kept for `execute_task` and the demo.

These are thin wrappers over the structured modules (`status.py`, `diff.py`).
New code should call those directly, or use the `git.*` gateway tools.
"""

from pathlib import Path

from execution.git.diff import get_diff
from execution.git.status import get_status

_UNBOUNDED = 50_000_000


def git_status(repo_path: str) -> dict:
    """Return the current Git branch, clean flag and changed files."""

    status = get_status(Path(repo_path).resolve())
    modified = [entry["path"] for entry in status["entries"]]
    modified += status["untracked"]

    return {
        "branch": status["branch"] or "",
        "clean": status["clean"],
        "modified_files": modified,
    }


def git_diff(repo_path: str) -> str:
    """Return the unstaged diff of tracked files."""

    return get_diff(
        Path(repo_path).resolve(),
        mode="unstaged",
        include_untracked=False,
        max_bytes=_UNBOUNDED,
    )["diff"]
