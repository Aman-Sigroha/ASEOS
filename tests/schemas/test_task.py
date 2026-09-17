import pytest
from pydantic import ValidationError

from runtime.schemas.task import Task


def test_task_creation():
    task = Task(
        id="task-001",
        description="Fix the login timeout bug",
        workspace_path="C:/projects/my-app",
    )

    assert task.id == "task-001"
    assert task.description == "Fix the login timeout bug"
    assert task.workspace_path == "C:/projects/my-app"


def test_task_rejects_empty_id():
    with pytest.raises(ValidationError):
        Task(
            id="",
            description="Fix the bug",
            workspace_path="C:/projects/app",
        )


def test_task_rejects_empty_description():
    with pytest.raises(ValidationError):
        Task(
            id="task-001",
            description="",
            workspace_path="C:/projects/app",
        )


def test_task_rejects_empty_workspace_path():
    with pytest.raises(ValidationError):
        Task(
            id="task-001",
            description="Fix the bug",
            workspace_path="",
        )
