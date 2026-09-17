import pytest

from runtime.decision.decision import Decision
from runtime.decision.engine import DecisionEngine
from runtime.decision.context import DecisionContext
from runtime.schemas.understanding import TaskUnderstanding


class TestDecisionEngine(DecisionEngine):
    async def decide(self, context):
        return Decision(
            decision_type="COMPLETE",
            reason="Test decision.",
            confidence=1.0,
        )


def test_decision_engine_is_abstract():

    assert DecisionEngine.__abstractmethods__ == {"decide"}


@pytest.mark.asyncio
async def test_decision_engine_can_be_implemented():

    engine = TestDecisionEngine()

    decision = await engine.decide(None)

    assert decision.decision_type == "COMPLETE"
    assert decision.reason == "Test decision."
    assert decision.confidence == 1.0


@pytest.mark.asyncio
async def test_decision_engine_can_be_implemented():

    engine = TestDecisionEngine()

    context = DecisionContext(
        task_id="task-001",
        task_description="Test task",
        understanding=TaskUnderstanding(
            goal="Test goal",
            expected_outcome="Test outcome",
        ),
        plan_goal="Test plan",
        status="READY",
    )

    decision = await engine.decide(context)

    assert decision.decision_type == "COMPLETE"
    assert decision.reason == "Test decision."
    assert decision.confidence == 1.0
