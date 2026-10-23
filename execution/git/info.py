"""Read-only repository identity and metadata."""

import os
from pathlib import Path
from typing import Any

from execution.git.repo import (
    GitError,
    ensure_repo_root,
    git_dir,
    head_sha,
    repo_state,
    run_git,
)

MAX_BRANCHES = 100
MAX_COMMITS = 20
_FIELD = "\x1f"
_RECORD = "\x1e"


def _config_set(workspace: Path, key: str) -> bool:
    result = run_git(workspace, ["config", "--get", key], ok_codes=(0, 1))
    return bool(result.stdout.strip())


def _config_value(workspace: Path, key: str) -> str | None:
    result = run_git(workspace, ["config", "--get", key], ok_codes=(0, 1))
    return result.stdout.strip() or None


def default_branch(workspace: Path, branches: list[str]) -> str | None:
    """origin/HEAD if known, else the first of main/master that exists."""

    result = run_git(
        workspace,
        ["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"],
        ok_codes=(0, 1),
    )
    ref = result.stdout.strip()

    if ref:
        return ref.removeprefix("refs/remotes/origin/")

    for candidate in ("main", "master"):
        if candidate in branches:
            return candidate

    return None


def list_branches(workspace: Path, limit: int) -> tuple[list[dict], bool]:
    fmt = _FIELD.join(
        ["%(HEAD)", "%(refname:short)", "%(objectname)", "%(upstream:short)"]
    )
    out = run_git(
        workspace,
        ["for-each-ref", f"--format={fmt}", "refs/heads"],
    ).stdout
    branches = []

    for line in out.splitlines():
        marker, name, sha, upstream = line.split(_FIELD)
        branches.append(
            {
                "name": name,
                "sha": sha,
                "current": marker == "*",
                "upstream": upstream or None,
            }
        )

    return branches[:limit], len(branches) > limit


def recent_commits(workspace: Path, limit: int) -> list[dict[str, Any]]:
    if head_sha(workspace) is None:
        return []

    fmt = _FIELD.join(["%H", "%an", "%aI", "%s"]) + _RECORD
    out = run_git(
        workspace, ["log", f"-{limit}", f"--format={fmt}", "--no-color"]
    ).stdout
    commits = []

    for record in out.split(_RECORD):
        record = record.strip("\n")

        if not record:
            continue

        sha, author, date, subject = record.split(_FIELD)
        commits.append(
            {"sha": sha, "author": author, "date": date, "subject": subject}
        )

    return commits


def get_info(
    workspace: Path, max_branches: int = 50, max_commits: int = 5
) -> dict[str, Any]:
    """Repository identity, HEAD, branches, recent commits, relevant config."""

    ensure_repo_root(workspace)
    max_branches = min(max_branches, MAX_BRANCHES)
    max_commits = min(max_commits, MAX_COMMITS)
    directory = git_dir(workspace)
    common = run_git(workspace, ["rev-parse", "--git-common-dir"]).stdout.strip()
    common_dir = (workspace / common).resolve()
    branches, branches_truncated = list_branches(workspace, max_branches)
    names = [b["name"] for b in branches]
    head = head_sha(workspace)
    current = next((b["name"] for b in branches if b["current"]), None)

    try:
        origin = _config_value(workspace, "remote.origin.url")
    except GitError:
        origin = None

    return {
        "repository_root": str(workspace),
        "git_dir": str(directory),
        "common_git_dir": str(common_dir),
        "is_linked_worktree": not os.path.samefile(directory, common_dir),
        "head_sha": head,
        "branch": current,
        "detached": head is not None and current is None,
        "unborn": head is None,
        "default_branch": default_branch(workspace, names),
        "state": repo_state(workspace),
        "branches": branches,
        "branches_truncated": branches_truncated,
        "recent_commits": recent_commits(workspace, max_commits),
        "has_remote_origin": origin is not None,
        "config": {
            "user_name_set": _config_set(workspace, "user.name"),
            "user_email_set": _config_set(workspace, "user.email"),
            "commit_gpgsign": _config_value(workspace, "commit.gpgsign"),
        },
    }
