"""Scoped, evidence-recording Git mutations.

Rules that protect existing user work:
- Every operation names its exact paths; there is no "stage everything",
  no globbing (`--literal-pathspecs`), no directories.
- `commit` refuses unless the index contains *exactly* the named paths, so
  changes the user staged themselves are never swept into an agent commit.
- `discard` only touches tracked, unstaged files, and refuses unless the
  discarded diff can be returned in full (so it can be re-applied).
- Nothing here switches branches, resets, cleans, pushes or rewrites history.
- Each result lists `other_changes`: dirty paths outside the operation's
  scope, observed before the operation ran.
"""

import hashlib
from pathlib import Path
from typing import Any

from execution.git.diff import get_diff
from execution.git.repo import (
    GitError,
    clean_relative_paths,
    ensure_repo_root,
    head_sha,
    repo_state,
    run_git,
    resolve_commit,
)
from execution.git.status import dirty_paths, get_status

MAX_PATHS = 200
MAX_MESSAGE_CHARS = 4000
MAX_DISCARD_DIFF_BYTES = 1_000_000
WRITE_TIMEOUT_SECONDS = 60


def _paths(raw: list[str]) -> list[str]:
    if not raw:
        raise GitError("INVALID_ARGUMENTS", "paths must not be empty")

    if len(raw) > MAX_PATHS:
        raise GitError(
            "RESOURCE_LIMIT", f"At most {MAX_PATHS} paths per operation."
        )

    cleaned = clean_relative_paths(raw)

    if len(set(cleaned)) != len(cleaned):
        raise GitError("INVALID_ARGUMENTS", "paths contains duplicates")

    if any(p in (".", "") or p.endswith("/") for p in cleaned):
        raise GitError(
            "INVALID_ARGUMENTS",
            "Name individual files; directories and '.' are not allowed.",
        )

    return cleaned


def _require_ready(workspace: Path, status: dict, *, allow_detached: bool) -> None:
    if status["state"] is not None:
        raise GitError(
            "GIT_FAILURE",
            f"Repository is in the middle of a {status['state']}; "
            "finish or abort it first.",
            {"state": status["state"]},
        )

    if status["conflicted"]:
        raise GitError(
            "GIT_FAILURE",
            "Repository has unresolved conflicts.",
            {"conflicted": status["conflicted"]},
        )

    if status["detached"] and not allow_detached:
        raise GitError(
            "GIT_FAILURE", "HEAD is detached; refusing to commit on it."
        )


def _other_changes(status: dict, scope: list[str]) -> dict[str, Any]:
    others = sorted(dirty_paths(status) - set(scope))
    return {"other_changes": others[:100], "other_changes_truncated": len(others) > 100}


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _tracked(workspace: Path, path: str) -> bool:
    result = run_git(
        workspace,
        ["ls-files", "--error-unmatch", "--", path],
        ok_codes=(0, 1),
    )
    return result.returncode == 0


def stage_paths(
    workspace: Path,
    paths: list[str],
    expected_sha256: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """Stage exactly these files.

    `expected_sha256` maps path -> hash of the working file the caller last
    produced (None = the file is expected to be deleted). A mismatch means
    someone else changed it since, and nothing is staged.
    """

    ensure_repo_root(workspace)
    paths = _paths(paths)
    before = get_status(workspace)
    _require_ready(workspace, before, allow_detached=True)
    expected = expected_sha256 or {}
    unknown = set(expected) - set(paths)

    if unknown:
        raise GitError(
            "INVALID_ARGUMENTS",
            f"expected_sha256 lists paths not being staged: {sorted(unknown)}",
        )

    for path in paths:
        target = (workspace / path).resolve()

        if not target.is_relative_to(workspace):
            raise GitError("PERMISSION_DENIED", f"{path}: escapes the repository")

        if target.is_dir():
            raise GitError(
                "INVALID_ARGUMENTS", f"{path}: is a directory; name files"
            )

        if not target.exists() and not _tracked(workspace, path):
            raise GitError("INVALID_ARGUMENTS", f"{path}: no such file")

        if path in expected and _sha256(target) != (
            expected[path].lower() if expected[path] else None
        ):
            raise GitError(
                "PATCH_CONFLICT",
                f"{path}: changed since you produced it; nothing was staged.",
                {"path": path, "actual_sha256": _sha256(target)},
            )

    run_git(
        workspace,
        ["add", "--", *paths],
        read_only=False,
        timeout=WRITE_TIMEOUT_SECONDS,
    )
    after = get_status(workspace)
    return {
        "staged_paths": paths,
        "staged_now": after["staged"],
        **_other_changes(before, paths),
        "status_after": after["counts"],
    }


def commit_paths(
    workspace: Path, message: str, paths: list[str]
) -> dict[str, Any]:
    """Commit exactly `paths`; the index must contain nothing else."""

    ensure_repo_root(workspace)
    paths = _paths(paths)
    message = message.strip()

    if not message or "\0" in message:
        raise GitError("INVALID_ARGUMENTS", "Commit message must not be empty")

    if len(message) > MAX_MESSAGE_CHARS:
        raise GitError("RESOURCE_LIMIT", "Commit message is too long")

    before = get_status(workspace)
    _require_ready(workspace, before, allow_detached=False)

    staged = run_git(
        workspace, ["diff", "--cached", "--name-only", "--no-renames", "-z"]
    ).stdout
    staged_set = {p for p in staged.split("\0") if p}
    wanted = set(paths)

    if staged_set != wanted:
        raise GitError(
            "GIT_FAILURE",
            "The index does not match the requested paths; refusing to "
            "commit changes that were not named.",
            {
                "unexpected_staged": sorted(staged_set - wanted),
                "missing_staged": sorted(wanted - staged_set),
            },
        )

    run_git(
        workspace,
        ["commit", "-m", message],
        read_only=False,
        timeout=WRITE_TIMEOUT_SECONDS,
    )
    sha = head_sha(workspace)
    after = get_status(workspace)
    return {
        "commit": sha,
        "branch": before["branch"],
        "committed_paths": paths,
        "subject": message.splitlines()[0],
        **_other_changes(before, paths),
        "status_after": after["counts"],
    }


def create_branch(
    workspace: Path, name: str, start_point: str | None = None
) -> dict[str, Any]:
    """Create a branch (does not check it out)."""

    ensure_repo_root(workspace)

    if not name or name.startswith("-") or "\0" in name:
        raise GitError("INVALID_ARGUMENTS", f"Invalid branch name: {name!r}")

    check = run_git(
        workspace, ["check-ref-format", "--branch", name], ok_codes=(0, 1, 128)
    )

    if check.returncode != 0:
        raise GitError("INVALID_ARGUMENTS", f"Invalid branch name: {name!r}")

    exists = run_git(
        workspace,
        ["show-ref", "--verify", "--quiet", f"refs/heads/{name}"],
        ok_codes=(0, 1),
    )

    if exists.returncode == 0:
        raise GitError(
            "GIT_FAILURE", f"Branch '{name}' already exists", {"branch": name}
        )

    commit = (
        resolve_commit(workspace, start_point)
        if start_point
        else head_sha(workspace)
    )

    if commit is None:
        raise GitError("GIT_FAILURE", "Repository has no commits yet")

    run_git(
        workspace,
        ["branch", "--no-track", name, commit],
        read_only=False,
        timeout=WRITE_TIMEOUT_SECONDS,
    )
    return {"branch": name, "commit": commit, "checked_out": False}


def discard_paths(workspace: Path, paths: list[str]) -> dict[str, Any]:
    """Discard unstaged edits to tracked files, returning the discarded diff.

    Refuses untracked, staged, conflicted or unchanged files, and refuses
    when the discarded diff is too large to return in full.
    """

    ensure_repo_root(workspace)
    paths = _paths(paths)
    before = get_status(workspace)
    _require_ready(workspace, before, allow_detached=True)
    by_path = {e["path"]: e for e in before["entries"]}

    for path in paths:
        entry = by_path.get(path)

        if path in before["untracked"]:
            raise GitError(
                "INVALID_ARGUMENTS", f"{path}: is untracked; refusing to delete it"
            )

        if entry is None or entry["worktree"] is None:
            raise GitError("INVALID_ARGUMENTS", f"{path}: has no unstaged changes")

        if entry["index"] is not None:
            raise GitError(
                "GIT_FAILURE",
                f"{path}: also has staged changes; refusing to discard.",
                {"path": path},
            )

    evidence = get_diff(
        workspace,
        mode="unstaged",
        paths=paths,
        include_untracked=False,
        max_bytes=MAX_DISCARD_DIFF_BYTES,
        context=3,
    )

    if evidence["truncated"]:
        raise GitError(
            "RESOURCE_LIMIT",
            "Discarded diff is too large to return in full, so it could not "
            "be recovered; discard fewer files.",
        )

    run_git(
        workspace,
        ["restore", "--worktree", "--", *paths],
        read_only=False,
        timeout=WRITE_TIMEOUT_SECONDS,
    )
    after = get_status(workspace)
    still = [p for p in paths if p in {e["path"] for e in after["entries"]}]

    if still:
        raise GitError(
            "INTERNAL_ERROR",
            f"Discard did not take effect for: {still}",
            {"discarded_diff": evidence["diff"]},
        )

    return {
        "discarded_paths": paths,
        "discarded_diff": evidence["diff"],
        "discarded_stats": evidence["stats"],
        **_other_changes(before, paths),
        "status_after": after["counts"],
    }
