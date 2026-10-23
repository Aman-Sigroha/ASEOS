from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

WorktreeStatus = Literal[
    "creating",  # registered, `git worktree add` not confirmed yet
    "active",  # created, work in progress
    "completed",  # task finished successfully; kept for review
    "failed",  # task failed; kept for debugging
    "abandoned",  # no longer expected to be used
    "missing",  # recorded, but the directory has disappeared
    "removing",  # removal started (crash here is recovered by reconcile)
    "removed",  # terminal
]

LIVE_STATUSES: tuple[str, ...] = (
    "creating",
    "active",
    "completed",
    "failed",
    "abandoned",
    "missing",
    "removing",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorktreeRecord(BaseModel):
    """Authoritative description of one managed worktree.

    The record, not a caller-supplied path, identifies the worktree.
    """

    worktree_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    repo_id: str = Field(min_length=1)
    repo_root: str
    path: str
    branch: str
    base_ref: str
    base_commit: str
    owner: str = ""
    status: WorktreeStatus = "creating"
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    removed_at: str | None = None
    note: str = ""
    backups: dict[str, str] = Field(default_factory=dict)
