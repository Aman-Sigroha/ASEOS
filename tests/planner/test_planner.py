import pytest


from typing import Any

from runtime.llm.mock import MockLLMClient
from runtime.planner.planner import Planner
from runtime.schemas.repository import RepositoryContext
from runtime.schemas.task import Task
from runtime.schemas.understanding import TaskUnderstanding
from runtime.schemas.plan import Plan


@pytest.mark.asyncio
async def test_planner_creates_plan():
    llm = MockLLMClient()
    planner = Planner(llm)

    task = Task(
        id="task-001",
        description="Fix the login timeout bug.",
        workspace_path="C:/projects/example",
    )

    understanding = TaskUnderstanding(
        goal="Fix the login timeout bug",
        expected_outcome="Authentication handles timeout correctly",
        constraints=["Do not break existing authentication"],
        verification_requirements=["Run authentication tests"],
    )

    repository_context = RepositoryContext(
        root="C:/projects/example",
        summary="Python authentication project",
    )

    result = await planner.create_plan(
        task=task,
        understanding=understanding,
        repository_context=repository_context,
    )

    assert isinstance(result, Plan)
    assert result.task_id == "task-001"
    assert result.goal == "Mock goal"
    assert len(result.steps) == 2

    assert result.steps[0].action_type == "SEARCH"
    assert result.steps[1].action_type == "READ"


class RecordingLLM:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] | None = None

    async def generate_structured(
        self,
        messages: list[dict[str, str]],
        response_model: type[Any],
        **kwargs: Any,
    ) -> Plan:
        self.messages = messages

        return Plan(
            task_id="task-001",
            goal="Recorded goal",
            steps=[],
        )


@pytest.mark.asyncio
async def test_planner_sends_task_context_to_llm():
    llm = RecordingLLM()
    planner = Planner(llm)

    task = Task(
        id="task-001",
        description="Fix the login timeout bug.",
        workspace_path="C:/projects/example",
    )

    understanding = TaskUnderstanding(
        goal="Fix login timeout",
        expected_outcome="Authentication works correctly",
        constraints=["Preserve existing behavior"],
        verification_requirements=["Run auth tests"],
    )

    repository_context = RepositoryContext(
        root="C:/projects/example",
        summary="Python authentication project",
    )

    await planner.create_plan(
        task=task,
        understanding=understanding,
        repository_context=repository_context,
    )

    assert llm.messages is not None

    user_message = llm.messages[1]["content"]

    assert "Fix the login timeout bug." in user_message
    assert "Fix login timeout" in user_message
    assert "Python authentication project" in user_message
    assert "C:/projects/example" in user_message
