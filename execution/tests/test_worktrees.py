from pathlib import Path
import subprocess

from execution.git.worktree import create_worktree, remove_worktree


def test_create_and_remove_worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    subprocess.run(
        ["git", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
    )

    subprocess.run(
        ["git", "config", "user.name", "ASEOS Test"],
        cwd=repo,
        check=True,
    )

    test_file = repo / "example.txt"
    test_file.write_text("hello")

    subprocess.run(
        ["git", "add", "."],
        cwd=repo,
        check=True,
    )

    subprocess.run(
        ["git", "commit", "-m", "initial commit"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    worktree_path = create_worktree(str(repo), "test-task")

    assert Path(worktree_path).exists()
    assert (Path(worktree_path) / "example.txt").exists()

    remove_worktree(str(repo), "test-task")

    assert not Path(worktree_path).exists()
