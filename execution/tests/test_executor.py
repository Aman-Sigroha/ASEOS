
import shutil
from pathlib import Path
from uuid import uuid4

from execution.executor import execute_task


def fixture_repo(tmp_path):
    source = Path("fixtures/repositories/calculator").resolve()
    target = tmp_path / "calculator"
    shutil.copytree(source, target)
    return str(target)


def test_execute_task(tmp_path):
    repo_path = fixture_repo(tmp_path)

    task_id = f"test-executor-{uuid4().hex[:8]}"

    patch = {
        "file": "calculator.py",
        "old": "return a * b",
        "new": "return a / b",
    }

    result = execute_task(
        repo_path=repo_path,
        task_id=task_id,
        patch=patch,
    )

    assert result["status"] == "PASS"
    assert result["patch"]["status"] == "PASS"
    assert result["tests"]["status"] == "PASS"
    assert "calculator.py" in result["diff"]
    assert "return a / b" in result["diff"]
    # the change is the deliverable, so the worktree is preserved for review
    assert result["worktree"]["preserved"] is True
    assert result["worktree"]["status"] == "completed"
    assert Path(result["worktree"]["path"]).is_dir()
