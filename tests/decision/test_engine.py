import pytest

from runtime.decision.decision import Decision
from runtime.decision.engine import DecisionEngine


class TestDecisionEngine(DecisionEngine):
    async def decide(self, state):
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
