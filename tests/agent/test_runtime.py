import pytest

from runtime.agent.mock_executor import MockActionExecutor
from runtime.agent.runtime import AgentRuntime
from runtime.llm.mock import MockLLMClient
from runtime.planner.actions import ActionGenerator
from runtime.planner.planner import Planner
from runtime.schemas.plan import Plan
from runtime.schemas.repository import RepositoryContext
from runtime.schemas.task import Task
from runtime.schemas.understanding import TaskUnderstanding
from runtime.task.understanding import TaskUnderstandingService


@pytest.mark.asyncio
async def test_agent_runtime_prepare():
    llm = MockLLMClient()

    understanding_service = TaskUnderstandingService(llm)
    planner = Planner(llm)
    action_generator = ActionGenerator()
    executor = MockActionExecutor()

    runtime = AgentRuntime(
        understanding_service=understanding_service,
        planner=planner,
        action_generator=action_generator,
        executor=executor,
    )

    task = Task(
        id="task-001",
        description="Fix the login timeout bug.",
        workspace_path="C:/projects/example",
    )

    repository_context = RepositoryContext(
        root="C:/projects/example",
        summary="Python authentication project",
    )

    state = await runtime.prepare(
        task=task,
        repository_context=repository_context,
    )

    assert state.task == task
    assert state.status == "READY"

    assert isinstance(
        state.understanding,
        TaskUnderstanding,
    )

    assert isinstance(
        state.plan,
        Plan,
    )

    assert len(state.actions) == 2

    assert state.actions[0].type == "SEARCH"
    assert state.actions[1].type == "READ"

    assert state.current_action_index == 0

    assert executor.executed_actions == []
