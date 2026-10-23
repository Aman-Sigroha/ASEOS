"""Backwards-compatible helpers over `WorktreeManager`.

New code should use `execution.worktrees.manager.WorktreeManager`, which
returns full records and supports assessment, reconciliation and
confirmed removal.
"""

from execution.worktrees.ids import (  # noqa: F401  (re-exported)
    WORKTREE_ROOT,
    validate_task_id,
)
from execution.worktrees.manager import WorktreeManager


def create_worktree(repo_path: str, task_id: str) -> str:
    """Create an isolated, registered worktree and return its path."""

    validate_task_id(task_id)
    return WorktreeManager(repo_path).create(task_id).path


def remove_worktree(
    repo_path: str, task_id: str, discard_confirmed: bool = False
) -> None:
    """Remove a task's worktree if it has nothing worth keeping.

    A worktree with uncommitted changes or unmerged commits is refused
    unless `discard_confirmed=True` (and is then backed up first). A task
    without a live worktree is a no-op.
    """

    validate_task_id(task_id)
    manager = WorktreeManager(repo_path)
    record = manager.find_live(task_id)

    if record is not None:
        manager.remove(record.worktree_id, discard_confirmed=discard_confirmed)
