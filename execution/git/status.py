"""Structured `git status` (porcelain v2)."""

from pathlib import Path
from typing import Any

from execution.git.repo import (
    ensure_repo_root,
    repo_state,
    run_git,
)

MAX_STATUS_ENTRIES = 500

_CODES = {
    "M": "modified",
    "A": "added",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "T": "typechange",
    "U": "unmerged",
}


def _state(code: str) -> str | None:
    return None if code == "." else _CODES.get(code, code)


def parse_porcelain_v2(output: str) -> dict[str, Any]:
    branch: dict[str, Any] = {
        "oid": None,
        "head": None,
        "upstream": None,
        "ahead": None,
        "behind": None,
    }
    entries: list[dict[str, Any]] = []
    untracked: list[str] = []
    records = output.split("\0")
    index = 0

    while index < len(records):
        record = records[index]
        index += 1

        if not record:
            continue

        if record.startswith("# "):
            parts = record[2:].split(" ")
            key = parts[0]

            if key == "branch.oid":
                branch["oid"] = None if parts[1] == "(initial)" else parts[1]
            elif key == "branch.head":
                branch["head"] = " ".join(parts[1:])
            elif key == "branch.upstream":
                branch["upstream"] = " ".join(parts[1:])
            elif key == "branch.ab":
                branch["ahead"] = int(parts[1].lstrip("+"))
                branch["behind"] = int(parts[2].lstrip("-"))

        elif record.startswith("1 "):
            parts = record.split(" ", 8)
            entries.append(_entry(parts[8], parts[1]))
        elif record.startswith("2 "):
            parts = record.split(" ", 9)
            original = records[index] if index < len(records) else None
            index += 1
            entries.append(_entry(parts[9], parts[1], original))
        elif record.startswith("u "):
            parts = record.split(" ", 10)
            entries.append(_entry(parts[10], parts[1], conflicted=True))
        elif record.startswith("? "):
            untracked.append(record[2:])

    return {"branch": branch, "entries": entries, "untracked": untracked}


def _entry(
    path: str,
    xy: str,
    original: str | None = None,
    conflicted: bool = False,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "path": path,
        "index": _state(xy[0]),
        "worktree": _state(xy[1]),
        "conflicted": conflicted or xy[0] == "U" or xy[1] == "U",
    }

    if original is not None:
        entry["original_path"] = original

    return entry


def get_status(workspace: Path, max_entries: int = MAX_STATUS_ENTRIES) -> dict:
    """Branch, upstream, staged/unstaged/untracked/conflicted paths."""

    ensure_repo_root(workspace)
    output = run_git(
        workspace,
        ["status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all"],
    ).stdout
    parsed = parse_porcelain_v2(output)
    entries = parsed["entries"]
    untracked = parsed["untracked"]
    branch = parsed["branch"]
    head = branch["head"]
    detached = head == "(detached)"

    staged = [e["path"] for e in entries if e["index"] is not None]
    unstaged = [e["path"] for e in entries if e["worktree"] is not None]
    conflicted = [e["path"] for e in entries if e["conflicted"]]
    total = len(entries) + len(untracked)

    return {
        "repository_root": str(workspace),
        "branch": None if detached else head,
        "detached": detached,
        "head_sha": branch["oid"],
        "upstream": branch["upstream"],
        "ahead": branch["ahead"],
        "behind": branch["behind"],
        "state": repo_state(workspace),
        "clean": total == 0,
        "counts": {
            "staged": len(staged),
            "unstaged": len(unstaged),
            "untracked": len(untracked),
            "conflicted": len(conflicted),
        },
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked[:max_entries],
        "conflicted": conflicted,
        "entries": entries[:max_entries],
        "truncated": total > max_entries or len(untracked) > max_entries,
    }


def dirty_paths(status: dict) -> set[str]:
    """Every path with any change (staged, unstaged or untracked)."""

    paths = {e["path"] for e in status["entries"]}
    paths.update(status["untracked"])

    for entry in status["entries"]:
        original = entry.get("original_path")

        if original:
            paths.add(original)

    return paths
