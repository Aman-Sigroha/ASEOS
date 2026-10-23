"""Structured `git diff` with an explicit comparison base."""

from pathlib import Path
from typing import Any

from execution.security.paths import is_secret_path
from execution.git.repo import (
    GitError,
    clean_relative_paths,
    ensure_repo_root,
    head_sha,
    resolve_commit,
    run_git,
)

DIFF_MODES = ("unstaged", "staged", "base")
DEFAULT_MAX_DIFF_BYTES = 200_000
MAX_UNTRACKED_FILES = 50
MAX_UNTRACKED_FILE_BYTES = 200_000
_STATUS_NAMES = {
    "A": "added",
    "M": "modified",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "T": "typechange",
}
_COMMON = ["--no-ext-diff", "--no-textconv", "--no-color", "-M"]


def _pathspec(paths: list[str] | None) -> list[str]:
    return ["--", *paths] if paths else []


def _parse_name_status(output: str) -> list[dict[str, Any]]:
    fields = [f for f in output.split("\0")]
    files = []
    i = 0

    while i < len(fields) and fields[i]:
        code = fields[i]
        letter = code[0]
        i += 1

        if letter in ("R", "C"):
            old, new = fields[i], fields[i + 1]
            i += 2
            files.append(
                {
                    "path": new,
                    "original_path": old,
                    "status": _STATUS_NAMES[letter],
                    "similarity": int(code[1:] or 0),
                }
            )
        else:
            files.append(
                {"path": fields[i], "status": _STATUS_NAMES.get(letter, letter)}
            )
            i += 1

    return files


def _parse_numstat(output: str) -> dict[str, tuple[int | None, int | None]]:
    """Map path -> (additions, deletions); None for binary files."""

    stats: dict[str, tuple[int | None, int | None]] = {}
    fields = output.split("\0")
    i = 0

    while i < len(fields) and fields[i]:
        added, deleted, path = fields[i].split("\t", 2)
        i += 1

        if path == "":  # rename: old and new follow as separate fields
            path = fields[i + 1]
            i += 2

        binary = added == "-"
        stats[path] = (
            None if binary else int(added),
            None if binary else int(deleted),
        )

    return stats


def _untracked_diff(
    workspace: Path, paths: list[str] | None, allow_secrets: bool = False
) -> tuple[str, list[dict[str, Any]], list[dict[str, str]]]:
    listing = run_git(
        workspace,
        ["ls-files", "--others", "--exclude-standard", "-z", *_pathspec(paths)],
    ).stdout
    names = [n for n in listing.split("\0") if n]
    text_parts: list[str] = []
    files: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for name in names:
        if not allow_secrets and is_secret_path(name):
            skipped.append({"path": name, "reason": "secret file; content not shown"})
            continue

        if len(files) >= MAX_UNTRACKED_FILES:
            skipped.append({"path": name, "reason": "too many untracked files"})
            continue

        target = workspace / name

        try:
            size = target.stat().st_size
            raw = target.read_bytes() if size <= MAX_UNTRACKED_FILE_BYTES else b""
        except OSError:
            skipped.append({"path": name, "reason": "unreadable"})
            continue

        if size > MAX_UNTRACKED_FILE_BYTES:
            skipped.append({"path": name, "reason": "file too large"})
            continue

        binary = b"\0" in raw[:8192]
        additions = None if binary else raw.count(b"\n") + (
            1 if raw and not raw.endswith(b"\n") else 0
        )
        files.append(
            {
                "path": name,
                "status": "added",
                "untracked": True,
                "additions": additions,
                "deletions": None if binary else 0,
                "binary": binary,
            }
        )
        result = run_git(
            workspace,
            ["diff", "--no-index", *_COMMON, "--", "/dev/null", name],
            ok_codes=(0, 1),
        )
        text_parts.append(result.stdout)

    return "".join(text_parts), files, skipped


def get_diff(
    workspace: Path,
    *,
    mode: str = "unstaged",
    base: str | None = None,
    paths: list[str] | None = None,
    include_untracked: bool = True,
    max_bytes: int = DEFAULT_MAX_DIFF_BYTES,
    context: int = 3,
    allow_secrets: bool = False,
) -> dict[str, Any]:
    """Diff with an explicit base.

    mode="unstaged": working tree vs the index   (base = the index)
    mode="staged":   index vs HEAD               (base = HEAD)
    mode="base":     working tree vs `base` ref  (base = that commit)

    Untracked files are shown as added files for "unstaged" and "base"
    (they are not part of `git diff` itself).
    """

    if mode not in DIFF_MODES:
        raise GitError("INVALID_ARGUMENTS", f"mode must be one of {DIFF_MODES}")

    ensure_repo_root(workspace)
    paths = clean_relative_paths(paths) if paths else None
    base_commit: str | None = None
    selector: list[str] = []

    if mode == "base":
        if not base:
            raise GitError("INVALID_ARGUMENTS", "mode 'base' requires a base ref")

        base_commit = resolve_commit(workspace, base)
        selector = [base_commit]
        base_description = f"commit {base_commit}"
    elif mode == "staged":
        selector = ["--cached"]
        base_commit = head_sha(workspace)
        base_description = "HEAD" if base_commit else "empty tree (no commits yet)"
    else:
        base_description = "the index (staged state)"

    flags = [*_COMMON, f"-U{context}"]
    tail = [*selector, *_pathspec(paths)]
    status_out = run_git(
        workspace, ["diff", "--name-status", "-z", "-M", "--no-ext-diff", *tail]
    ).stdout
    numstat_out = run_git(
        workspace, ["diff", "--numstat", "-z", "-M", "--no-ext-diff", *tail]
    ).stdout

    files = _parse_name_status(status_out)
    stats = _parse_numstat(numstat_out)
    secret_paths = set()

    if not allow_secrets:
        for file_info in files:
            names = [file_info["path"], file_info.get("original_path") or ""]

            if any(n and is_secret_path(n) for n in names):
                file_info["redacted"] = True
                secret_paths.update(n for n in names if n)

    if secret_paths:
        # Diff only the files whose content may be shown.
        keep = [
            n
            for f in files
            if not f.get("redacted")
            for n in (f["path"], f.get("original_path"))
            if n
        ]
        text = (
            run_git(
                workspace, ["diff", *flags, *selector, "--", *keep]
            ).stdout
            if keep
            else ""
        )
    else:
        text = run_git(workspace, ["diff", *flags, *tail]).stdout

    for file_info in files:
        added, deleted = stats.get(file_info["path"], (None, None))
        file_info["additions"] = added
        file_info["deletions"] = deleted
        file_info["binary"] = added is None

    skipped: list[dict[str, str]] = []

    if include_untracked and mode in ("unstaged", "base"):
        extra_text, extra_files, skipped = _untracked_diff(
            workspace, paths, allow_secrets
        )
        text += extra_text
        files += extra_files

    encoded = text.encode("utf-8")
    truncated = len(encoded) > max_bytes

    if truncated:
        text = encoded[:max_bytes].decode("utf-8", errors="ignore")

    return {
        "mode": mode,
        "base": base_description,
        "base_commit": base_commit,
        "files": files,
        "changed_files": [f["path"] for f in files],
        "stats": {
            "files": len(files),
            "additions": sum(f["additions"] or 0 for f in files),
            "deletions": sum(f["deletions"] or 0 for f in files),
        },
        "diff": text,
        "truncated": truncated,
        "total_bytes": len(encoded),
        "skipped_untracked": skipped,
    }
