import pytest

from runtime.agent.mock_executor import MockActionExecutor
from runtime.agent.runtime import AgentRuntime
from runtime.decision.llm import LLMDecisionEngine
from runtime.llm.mock import MockLLMClient
from runtime.planner.actions import ActionGenerator
from runtime.planner.planner import Planner
from runtime.schemas.repository import RepositoryContext
from runtime.schemas.task import Task
from runtime.task.understanding import TaskUnderstandingService
from runtime.llm.base import LLMClient


class SequentialDecisionLLM(MockLLMClient):
    def __init__(self):
        self.decision_calls = 0

    async def generate_structured(
        self,
        messages,
        response_model,
        **kwargs,
    ):
        if response_model.__name__ == "Decision":
            self.decision_calls += 1

            if self.decision_calls == 1:
                return response_model.model_validate(
                    {
                        "decision_type": "EXECUTE_ACTION",
                        "action_id": "step-1",
                        "reason": "Execute the search action first.",
                        "confidence": 0.95,
                    }
                )

            if self.decision_calls == 2:
                return response_model.model_validate(
                    {
                        "decision_type": "EXECUTE_ACTION",
                        "action_id": "step-2",
                        "reason": "Read the implementation after searching.",
                        "confidence": 0.95,
                    }
                )

            return response_model.model_validate(
                {
                    "decision_type": "COMPLETE",
                    "reason": "All planned actions have been executed.",
                    "confidence": 0.95,
                }
            )

        return await super().generate_structured(
            messages=messages,
            response_model=response_model,
            **kwargs,
        )


@pytest.mark.asyncio
async def test_agent_runtime_uses_llm_decision_engine():
    llm = SequentialDecisionLLM()

    understanding_service = TaskUnderstandingService(llm)
    planner = Planner(llm)
    action_generator = ActionGenerator()
    executor = MockActionExecutor()

    decision_engine = LLMDecisionEngine(llm)

    runtime = AgentRuntime(
        understanding_service=understanding_service,
        planner=planner,
        action_generator=action_generator,
        executor=executor,
        decision_engine=decision_engine,
    )

    task = Task(
        id="task-001",
        description="Fix the calculator bug",
        workspace_path="/workspace",
    )

    repository_context = RepositoryContext(
        root="/workspace",
        summary="Python calculator project",
    )

    state = await runtime.run(
        task=task,
        repository_context=repository_context,
    )

    assert state.status == "COMPLETED"

    assert len(state.execution_results) == 2

    assert [result.action_id for result in state.execution_results] == [
        "step-1",
        "step-2",
    ]

    assert [action.id for action in executor.executed_actions] == [
        "step-1",
        "step-2",
    ]


@pytest.mark.asyncio
async def test_agent_runtime_rejects_invalid_llm_decision():
    class InvalidDecisionLLM(MockLLMClient):
        async def generate_structured(
            self,
            messages,
            response_model,
            **kwargs,
        ):
            if response_model.__name__ == "Decision":
                return response_model.model_validate(
                    {
                        "decision_type": "EXECUTE_ACTION",
                        "action_id": "delete-everything",
                        "reason": "Execute the requested action.",
                        "confidence": 0.99,
                    }
                )

            return await super().generate_structured(
                messages=messages,
                response_model=response_model,
                **kwargs,
            )

    llm = InvalidDecisionLLM()

    runtime = AgentRuntime(
        understanding_service=TaskUnderstandingService(llm),
        planner=Planner(llm),
        action_generator=ActionGenerator(),
        executor=MockActionExecutor(),
        decision_engine=LLMDecisionEngine(llm),
    )

    task = Task(
        id="task-001",
        description="Fix the calculator bug",
        workspace_path="/workspace",
    )

    repository_context = RepositoryContext(
        root="/workspace",
        summary="Python calculator project",
    )

    state = await runtime.run(
        task=task,
        repository_context=repository_context,
    )

    assert state.status == "FAILED"
    assert state.execution_results == []
