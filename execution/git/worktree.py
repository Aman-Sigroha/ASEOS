import subprocess
from pathlib import Path


WORKTREE_ROOT = ".aseos_worktrees"


def create_worktree(repo_path: str, task_id: str) -> str:
    """
    Create an isolated Git worktree for a task.

    Returns the path to the created worktree.
    """

    repo = Path(repo_path).resolve()
    worktree_dir = repo / WORKTREE_ROOT / task_id

    worktree_dir.parent.mkdir(parents=True, exist_ok=True)

    subprocess.run(
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
        check=True,
    )

    return str(worktree_dir)


def remove_worktree(repo_path: str, task_id: str) -> None:
    """
    Remove an isolated Git worktree for a task.
    """

    repo = Path(repo_path).resolve()
    worktree_dir = repo / WORKTREE_ROOT / task_id

    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "remove",
            "--force",
            str(worktree_dir),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
