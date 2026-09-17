import pytest

from runtime.decision.context import DecisionContext
from runtime.decision.decision import Decision
from runtime.decision.llm import LLMDecisionEngine
from runtime.llm.mock import MockLLMClient
from runtime.schemas.action import Action
from runtime.schemas.understanding import TaskUnderstanding
from runtime.llm.router import ModelRouter
from runtime.schemas.execution import ExecutionResult


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


def test_llm_decision_engine_requires_llm_or_router():
    with pytest.raises(
        ValueError,
        match="Either llm or router must be provided",
    ):
        LLMDecisionEngine()


def test_llm_decision_engine_rejects_both_llm_and_router():
    llm = MockLLMClient()

    router = ModelRouter(
        clients={"default": llm},
        default_model="default",
    )

    with pytest.raises(
        ValueError,
        match="either llm or router",
    ):
        LLMDecisionEngine(
            llm=llm,
            router=router,
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


@pytest.mark.asyncio
async def test_llm_decision_engine_uses_model_router():
    class RecordingLLM(MockLLMClient):
        def __init__(self):
            self.calls = 0

        async def generate_structured(
            self,
            messages,
            response_model,
            **kwargs,
        ):
            self.calls += 1

            return response_model.model_validate(
                {
                    "decision_type": "EXECUTE_ACTION",
                    "action_id": "step-1",
                    "reason": "Execute the first action.",
                    "confidence": 0.95,
                }
            )

    default_llm = RecordingLLM()
    reasoning_llm = RecordingLLM()

    router = ModelRouter(
        clients={
            "default": default_llm,
            "reasoning": reasoning_llm,
        },
        default_model="default",
    )

    engine = LLMDecisionEngine(router=router)

    decision = await engine.decide(make_context())

    assert decision.decision_type == "EXECUTE_ACTION"
    assert default_llm.calls == 1
    assert reasoning_llm.calls == 0


@pytest.mark.asyncio
async def test_llm_decision_engine_routes_high_complexity_to_reasoning_model():
    class RecordingLLM(MockLLMClient):
        def __init__(self):
            self.calls = 0

        async def generate_structured(
            self,
            messages,
            response_model,
            **kwargs,
        ):
            self.calls += 1

            return response_model.model_validate(
                {
                    "decision_type": "REPLAN",
                    "reason": "The previous verification failed.",
                    "confidence": 0.96,
                }
            )

    default_llm = RecordingLLM()
    reasoning_llm = RecordingLLM()

    router = ModelRouter(
        clients={
            "default": default_llm,
            "reasoning": reasoning_llm,
        },
        default_model="default",
    )

    context = make_context()

    # Inject verification evidence to represent a difficult decision.
    context.verification_result = None

    context.current_plan_results = [
        # Deliberately use a failed result to force HIGH complexity.
        ExecutionResult(
            action_id="step-1",
            success=False,
            stderr="Execution failed",
            exit_code=1,
        )
    ]

    engine = LLMDecisionEngine(router=router)

    decision = await engine.decide(context)

    assert decision.decision_type == "REPLAN"
    assert default_llm.calls == 0
    assert reasoning_llm.calls == 1
