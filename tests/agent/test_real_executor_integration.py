import pytest

from runtime.agent.real_executor import RealActionExecutor
from runtime.agent.runtime import AgentRuntime
from runtime.llm.mock import MockLLMClient
from runtime.planner.actions import ActionGenerator
from runtime.planner.planner import Planner
from runtime.schemas.plan import Plan, PlanStep
from runtime.schemas.repository import RepositoryContext
from runtime.schemas.task import Task
from runtime.task.understanding import TaskUnderstandingService


class FixedPlanLLM(MockLLMClient):
    async def generate_structured(
        self,
        messages,
        response_model,
        **kwargs,
    ):
        if response_model is Plan:
            return Plan(
                task_id="real-execution-test",
                goal="Change multiplication to division in example.py.",
                steps=[

                    PlanStep(
                        id="read-1",
                        description="Read the sample file",
                        action_type="READ",
                        parameters={"path": "example.py"},
                    ),
                    PlanStep(
                        id="edit-1",
                        description="Correct the multiplication operator",
                        action_type="EDIT",
                        parameters={
                            "file": "example.py",
                            "old": "return a * b",
                            "new": "return a / b",
                        },
                    ),
                    PlanStep(
                        id="read-2",
                        description="Verify the edited file",
                        action_type="READ",
                        parameters={"path": "example.py"},
                    ),
                ]
            )

        return await super().generate_structured(
            messages=messages,
            response_model=response_model,
            **kwargs,
        )


@pytest.mark.asyncio
async def test_runtime_executes_real_actions_in_workspace(tmp_path):
    target = tmp_path / "example.py"
    target.write_text(
        "def calculate(a, b):\n    return a * b\n",
        encoding="utf-8",
    )

    llm = FixedPlanLLM()
    executor = RealActionExecutor(str(tmp_path))

    runtime = AgentRuntime(
        understanding_service=TaskUnderstandingService(llm),
        planner=Planner(llm),
        action_generator=ActionGenerator(),
        executor=executor,
    )

    task = Task(
        id="real-execution-test",
        description="Change calculate to divide instead of multiply.",
        workspace_path=str(tmp_path),
    )

    repository_context = RepositoryContext(
        root=str(tmp_path),
        summary="A small Python project containing example.py.",
    )

    state = await runtime.run(
        task=task,
        repository_context=repository_context,
    )

    assert state.status == "COMPLETED"
    assert len(state.execution_results) == 3
    assert all(result.success for result in state.execution_results)

    contents = target.read_text(encoding="utf-8")
    assert "return a / b" in contents
    assert "return a * b" not in contents
    assert "return a / b" in state.execution_results[2].stdout
