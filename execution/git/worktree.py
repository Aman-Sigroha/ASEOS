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
    worktree_dir = (repo / WORKTREE_ROOT / task_id).resolve()
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
