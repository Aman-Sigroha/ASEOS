"""Tell other roles that a workspace changed (the Role 3 interface).

Codebase intelligence keeps an index per workspace and needs to know when it
is stale. The gateway publishes a `WorkspaceChange` after every action that
changed (or may have changed) a workspace; a listener invalidates or refreshes
whatever it keeps.

Identity. A change names its workspace three ways, so a consumer can key an
index however it likes:
  * `workspace`     absolute path of the checkout or worktree,
  * `workspace_id`  stable hash of that path (also names the artifact scope),
  * `repo_id`       hash of the repository's common git dir: the same for the
                    main checkout and all of its worktrees,
and for a managed worktree also `worktree` (id, branch, base commit).

Delivery is best effort and in-process: listeners run synchronously on the
gateway's thread after the action finished, a listener that raises is ignored,
and nothing is queued across restarts. A slow listener slows the caller; hand
off to a queue if the work is heavy. Because a missed event is possible, a
consumer that needs certainty compares `gateway.workspace_version()` with the
version it indexed.

What counts as a change:
  files_changed       exact `paths` were written, created, deleted or discarded
  committed           a commit was made (`head` is the new commit)
  worktree_created    a managed worktree appeared
  worktree_removed    a managed worktree went away
  unknown_changes     a program ran (shell.run); it may have changed anything,
                      so `paths` is empty and the consumer should rescan
Read-only actions, test runs (which only write ignored caches) and index-only
git operations publish nothing.
"""

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from execution.git.repo import GitError, run_git
from runtime.schemas.action import Action
from runtime.schemas.execution import ExecutionResult

ChangeKind = Literal[
    "files_changed",
    "committed",
    "worktree_created",
    "worktree_removed",
    "unknown_changes",
]

MAX_PATHS = 1000


class WorkspaceChange(BaseModel):
    kind: ChangeKind
    workspace: str
    workspace_id: str
    repo_id: str | None = None
    task_id: str | None = None
    action_id: str
    tool: str | None = None
    paths: list[str] = Field(default_factory=list)
    paths_truncated: bool = False
    head: str | None = None
    worktree: dict[str, Any] | None = None
    at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


ChangeListener = Callable[[WorkspaceChange], None]


def repo_id_of(workspace: Path | str) -> str | None:
    """Identity shared by a repository's main checkout and all its worktrees
    (the same value `WorktreeManager.repo_id` uses), or None outside a repo."""

    try:
        workspace = Path(workspace).resolve()
        common = run_git(workspace, ["rev-parse", "--git-common-dir"], timeout=10).stdout.strip()
    except (GitError, OSError):
        return None

    resolved = (workspace / common).resolve()
    return hashlib.sha256(os.path.normcase(str(resolved)).encode("utf-8")).hexdigest()[:16]


def _strings(value: Any) -> list[str]:
    return [item for item in (value or []) if isinstance(item, str)]


def changes_from(
    action: Action,
    result: ExecutionResult,
    *,
    workspace: Path,
    workspace_id: str,
    task_id: str | None,
    repo_id: str | None,
) -> list[WorkspaceChange]:
    """The changes a finished action published (empty for most actions)."""

    data = result.data or {}
    base: dict[str, Any] = {
        "workspace": str(workspace),
        "workspace_id": workspace_id,
        "repo_id": repo_id,
        "task_id": task_id,
        "action_id": action.id,
        "tool": result.tool,
    }

    def change(kind: ChangeKind, paths: list[str] | None = None, **extra: Any) -> WorkspaceChange:
        paths = paths or []
        return WorkspaceChange(
            kind=kind,
            paths=paths[:MAX_PATHS],
            paths_truncated=len(paths) > MAX_PATHS,
            **base,
            **extra,
        )

    if action.type == "RUN_COMMAND":
        # A program may change anything, even when it then failed.
        if data.get("not_started"):
            return []

        return [change("unknown_changes")]

    if not result.success:
        return []

    if action.type in ("EDIT", "WRITE"):
        return [change("files_changed", _strings(data.get("changed_files")))]

    if action.type == "APPLY_PATCH":
        files = [f.get("path") for f in data.get("files", []) if isinstance(f, dict)]
        return [change("files_changed", _strings(files))]

    if action.type == "GIT_DISCARD":
        return [change("files_changed", _strings(data.get("discarded_paths")))]

    if action.type == "GIT_COMMIT":
        return [
            change(
                "committed",
                _strings(data.get("committed_paths")),
                head=data.get("commit"),
            )
        ]

    if action.type == "WORKTREE_CREATE":
        record = {
            key: data.get(key)
            for key in ("worktree_id", "task_id", "repo_id", "path", "branch", "base_commit")
        }
        return [change("worktree_created", worktree=record)]

    if action.type == "WORKTREE_REMOVE":
        if data.get("already"):
            return []  # it was already gone: nothing changed

        record = {"worktree_id": action.payload.get("worktree_id")}
        return [change("worktree_removed", worktree=record)]

    return []
