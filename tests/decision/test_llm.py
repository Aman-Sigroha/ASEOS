import pytest

from runtime.decision.context import DecisionContext
from runtime.decision.decision import Decision
from runtime.decision.llm import LLMDecisionEngine
from runtime.llm.mock import MockLLMClient
from runtime.schemas.action import Action
from runtime.schemas.understanding import TaskUnderstanding


def make_context() -> DecisionContext:
    return DecisionContext(
        task_id="task-001",
        task_description="Fix the calculator divide function.",
        understanding=TaskUnderstanding(
            goal="Fix division behavior.",
            expected_outcome="Division works correctly.",
            constraints=["Do not break existing functionality."],
            verification_requirements=["Run calculator tests."],
        ),
        repository_summary="Python calculator project.",
        plan_goal="Fix the divide function and verify it.",
        actions=[
            Action(
                id="step-1",
                type="SEARCH",
                payload={"query": "divide"},
            ),
            Action(
                id="step-2",
                type="READ",
                payload={"path": "src/calculator.py"},
            ),
        ],
        current_action_index=0,
        execution_results=[],
        current_plan_results=[],
        verification_result=None,
        replan_count=0,
        status="EXECUTING",
    )


@pytest.mark.asyncio
async def test_llm_decision_engine_returns_decision():
    engine = LLMDecisionEngine(MockLLMClient())

    decision = await engine.decide(make_context())

    assert isinstance(decision, Decision)
    assert decision.decision_type == "EXECUTE_ACTION"
    assert decision.action_id == "step-1"


@pytest.mark.asyncio
async def test_llm_decision_engine_sends_context_to_llm():
    class RecordingLLM(MockLLMClient):
        def __init__(self):
            self.messages = None

        async def generate_structured(
            self,
            messages,
            response_model,
            **kwargs,
        ):
            self.messages = messages

            return response_model.model_validate(
                {
                    "decision_type": "EXECUTE_ACTION",
                    "action_id": "step-1",
                    "reason": "The search action should be executed first.",
                    "confidence": 0.95,
                }
            )

    llm = RecordingLLM()
    engine = LLMDecisionEngine(llm)

    await engine.decide(make_context())

    assert llm.messages is not None
    assert len(llm.messages) == 2

    user_message = llm.messages[1]["content"]

    assert "Fix the calculator divide function." in user_message
    assert "Fix division behavior." in user_message
    assert "Python calculator project." in user_message
    assert "step-1" in user_message
    assert "step-2" in user_message
