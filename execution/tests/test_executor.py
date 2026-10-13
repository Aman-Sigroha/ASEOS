
from pathlib import Path
from uuid import uuid4

from execution.executor import execute_task


def test_execute_task():
    repo_path = str(
        Path("fixtures/repositories/calculator").resolve()
    )

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
