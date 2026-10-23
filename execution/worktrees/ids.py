import re

WORKTREE_ROOT = ".aseos_worktrees"
BRANCH_PREFIX = "aseos/"

# Conservative: the id becomes both a directory name and a Git branch name.
_TASK_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def validate_task_id(task_id: str) -> str:
    """Return task_id if it is safe to use as a directory and branch name."""

    if (
        not isinstance(task_id, str)
        or not _TASK_ID_PATTERN.fullmatch(task_id)
        or ".." in task_id
        or task_id.endswith((".", ".lock"))
    ):
        raise ValueError(
            "Invalid task_id: use 1-64 letters, digits, '.', '_' or '-', "
            "starting with a letter or digit."
        )

    return task_id


def branch_for(task_id: str) -> str:
    return f"{BRANCH_PREFIX}{validate_task_id(task_id)}"
