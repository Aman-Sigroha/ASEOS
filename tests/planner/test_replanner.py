import pytest

from runtime.llm.base import LLMClient
from runtime.planner.replanner import Replanner
from runtime.schemas.execution import ExecutionResult
from runtime.schemas.plan import Plan, PlanStep
from runtime.schemas.repository import RepositoryContext
from runtime.schemas.task import Task
from runtime.schemas.understanding import TaskUnderstanding


class RecordingLLM(LLMClient):
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    async def generate(
        self,
        messages: list[dict[str, str]],
        **kwargs,
    ) -> str:
        self.messages = messages
        return "unused"

    async def generate_structured(
        self,
        messages: list[dict[str, str]],
        response_model,
        **kwargs,
    ):
        self.messages = messages

        return response_model(
            task_id="task-001",
            goal="Replanned calculator fix",
            steps=[
                PlanStep(
                    id="replan-step-1",
                    description="Use a safer implementation",
                    action_type="EDIT",
                    parameters={
                        "path": "src/calculator.py",
                    },
                ),
                PlanStep(
                    id="replan-step-2",
                    description="Run calculator tests",
                    action_type="RUN_TEST",
                    parameters={
                        "command": "pytest tests/",
                    },
                ),
            ],
        )

    async def generate_with_tools(
        self,
        messages,
        tools,
        **kwargs,
    ):
        raise NotImplementedError


def make_task() -> Task:
    return Task(
        id="task-001",
        description="Fix the calculator bug.",
        workspace_path="/workspace",
    )


def make_understanding() -> TaskUnderstanding:
    return TaskUnderstanding(
        goal="Fix the calculator bug",
        expected_outcome="Calculator returns correct results",
        constraints=["Do not break existing behavior"],
        verification_requirements=["Run calculator tests"],
    )


def make_repository_context() -> RepositoryContext:
    return RepositoryContext(
        root="/workspace",
        summary="Python calculator project",
    )


def make_plan() -> Plan:
    return Plan(
        task_id="task-001",
        goal="Fix the calculator bug",
        steps=[
            PlanStep(
                id="step-1",
                description="Search for calculator implementation",
                action_type="SEARCH",
                parameters={
                    "query": "divide",
                },
            ),
        ],
    )


@pytest.mark.asyncio
async def test_replanner_returns_new_plan():

    llm = RecordingLLM()
    replanner = Replanner(llm)

    execution_results = [
        ExecutionResult(
            action_id="step-1",
            success=False,
            stderr="Search failed",
            exit_code=1,
            duration_ms=10,
        )
    ]

    plan = await replanner.replan(
        task=make_task(),
        understanding=make_understanding(),
        repository_context=make_repository_context(),
        current_plan=make_plan(),
        execution_results=execution_results,
    )

    assert plan.task_id == "task-001"
    assert plan.goal == "Replanned calculator fix"
    assert len(plan.steps) == 2
    assert plan.steps[0].id == "replan-step-1"
    assert plan.steps[1].action_type == "RUN_TEST"


@pytest.mark.asyncio
async def test_replanner_sends_failure_evidence_to_llm():

    llm = RecordingLLM()
    replanner = Replanner(llm)

    execution_results = [
        ExecutionResult(
            action_id="step-1",
            success=False,
            stderr="Search failed",
            exit_code=1,
            duration_ms=10,
        )
    ]

    await replanner.replan(
        task=make_task(),
        understanding=make_understanding(),
        repository_context=make_repository_context(),
        current_plan=make_plan(),
        execution_results=execution_results,
    )

    user_message = llm.messages[-1]["content"]

    assert "Search failed" in user_message
    assert "step-1" in user_message
    assert "Execution history" in user_message
    assert "Current plan" in user_message


@pytest.mark.asyncio
async def test_replanner_sends_repository_context_to_llm():

    llm = RecordingLLM()
    replanner = Replanner(llm)

    await replanner.replan(
        task=make_task(),
        understanding=make_understanding(),
        repository_context=make_repository_context(),
        current_plan=make_plan(),
        execution_results=[],
    )

    user_message = llm.messages[-1]["content"]

    assert "/workspace" in user_message
    assert "Python calculator project" in user_message
