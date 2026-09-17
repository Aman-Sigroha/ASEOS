import pytest

from runtime.llm.mock import MockLLMClient
from runtime.schemas.task import Task
from runtime.schemas.understanding import TaskUnderstanding
from runtime.task.understanding import TaskUnderstandingService


@pytest.mark.asyncio
async def test_task_understanding_service():
    llm = MockLLMClient()

    service = TaskUnderstandingService(llm)

    task = Task(
        id="task-001",
        description=("Fix the login timeout bug without breaking authentication."),
        workspace_path="C:/projects/example",
    )

    result = await service.understand(task)

    assert isinstance(result, TaskUnderstanding)

    assert result.goal == "Fix the login timeout bug"

    assert result.expected_outcome == "Authentication handles timeout correctly"

    assert result.constraints == ["Do not break existing authentication"]

    assert result.verification_requirements == ["Run authentication tests"]
