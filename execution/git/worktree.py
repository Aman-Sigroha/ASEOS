import re
import subprocess
from pathlib import Path


WORKTREE_ROOT = ".aseos_worktrees"

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


def _managed_worktree_dir(repo: Path, task_id: str) -> Path:
    """Resolve the task's worktree path, guaranteed inside the managed root."""

    root = (repo / WORKTREE_ROOT).resolve()
    worktree_dir = (root / validate_task_id(task_id)).resolve()

    if worktree_dir.parent != root:
        raise ValueError("Worktree path escapes the managed worktree root")

    return worktree_dir


def create_worktree(repo_path: str, task_id: str) -> str:
    """
    Create an isolated Git worktree for a task.

    Returns the path to the created worktree.
    """

    repo = Path(repo_path).resolve()
    worktree_dir = _managed_worktree_dir(repo, task_id)

    worktree_dir.parent.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "add",
            "-b",
            f"aseos/{task_id}",
            str(worktree_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to create worktree.\n"
            f"STDOUT: {result.stdout}\n"
            f"STDERR: {result.stderr}"
        )

    return str(worktree_dir)


def remove_worktree(repo_path: str, task_id: str) -> None:
    """
    Remove an isolated Git worktree, then its temporary task branch.
    """

    repo = Path(repo_path).resolve()
    worktree_dir = _managed_worktree_dir(repo, task_id)
    branch_name = f"aseos/{task_id}"

    # Check whether Git still registers this worktree.
    result = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    )

    registered_paths = {
        Path(line.removeprefix("worktree ")).resolve()
        for line in result.stdout.splitlines()
        if line.startswith("worktree ")
    }

    # Remove the worktree FIRST.
    if worktree_dir in registered_paths:
        subprocess.run(
            [
                "git", "-C", str(repo),
                "worktree", "remove", "--force", str(worktree_dir),
            ],
            capture_output=True,
            text=True,
            check=True,
        )

    # Prune stale worktree metadata before deleting the branch.
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "prune"],
        capture_output=True,
        text=True,
        check=True,
    )

    # Delete the temporary branch if it still exists.
    branch_check = subprocess.run(
        [
            "git", "-C", str(repo), "show-ref",
            "--verify", "--quiet", f"refs/heads/{branch_name}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if branch_check.returncode == 0:
        subprocess.run(
            ["git", "-C", str(repo), "branch", "-D", branch_name],
            capture_output=True,
            text=True,
            check=True,
        )
    elif branch_check.returncode != 1:
        raise RuntimeError(
            f"Could not check branch {branch_name}:\n{branch_check.stderr}"
        )
