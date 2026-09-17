import pytest
from pydantic import ValidationError

from runtime.decision.decision import Decision


def test_execute_action_decision():

    decision = Decision(
        decision_type="EXECUTE_ACTION",
        action_id="action-001",
        reason="The next planned action is required.",
        confidence=0.9,
    )

    assert decision.decision_type == "EXECUTE_ACTION"
    assert decision.action_id == "action-001"
    assert decision.reason == "The next planned action is required."
    assert decision.confidence == 0.9


def test_decision_can_request_approval():

    decision = Decision(
        decision_type="REQUEST_APPROVAL",
        reason="The proposed change requires human approval.",
        confidence=0.8,
    )

    assert decision.decision_type == "REQUEST_APPROVAL"
    assert decision.action_id is None


def test_decision_rejects_invalid_type():

    with pytest.raises(ValidationError):
        Decision(
            decision_type="DO_SOMETHING",
            reason="Invalid decision.",
            confidence=0.5,
        )


def test_decision_rejects_invalid_confidence():

    with pytest.raises(ValidationError):
        Decision(
            decision_type="COMPLETE",
            reason="Task completed.",
            confidence=1.5,
        )


def test_decision_rejects_empty_reason():

    with pytest.raises(ValidationError):
        Decision(
            decision_type="COMPLETE",
            reason="",
            confidence=1.0,
        )
