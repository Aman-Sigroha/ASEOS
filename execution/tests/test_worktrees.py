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


import pytest

from execution.git.worktree import validate_task_id


@pytest.mark.parametrize(
    "task_id",
    ["../escape", "a/b", "a\b", "", " ", "-flag", "a..b", "task.lock", "x" * 65, ".hidden"],
)
def test_validate_task_id_rejects_unsafe_values(task_id):
    with pytest.raises(ValueError):
        validate_task_id(task_id)


@pytest.mark.parametrize("task_id", ["fix-divide", "task_1", "T.2", "a"])
def test_validate_task_id_accepts_safe_values(task_id):
    assert validate_task_id(task_id) == task_id


def test_worktree_functions_reject_unsafe_task_id(tmp_path):
    with pytest.raises(ValueError):
        create_worktree(str(tmp_path), "../escape")

    with pytest.raises(ValueError):
        remove_worktree(str(tmp_path), "../escape")
