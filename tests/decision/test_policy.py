import pytest

from runtime.decision.context import DecisionContext
from runtime.decision.decision import Decision
from runtime.decision.policy import DecisionPolicy
from runtime.schemas.action import Action
from runtime.schemas.understanding import TaskUnderstanding


def make_context() -> DecisionContext:
    return DecisionContext(
        task_id="task-001",
        task_description="Fix the calculator bug.",
        understanding=TaskUnderstanding(
            goal="Fix calculator behavior.",
            expected_outcome="Calculator works correctly.",
            constraints=["Do not break existing behavior."],
            verification_requirements=["Run tests."],
        ),
        repository_summary="Python calculator project.",
        plan_goal="Fix and verify the calculator.",
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


def test_policy_allows_high_confidence_normal_action():
    policy = DecisionPolicy()

    decision = Decision(
        decision_type="EXECUTE_ACTION",
        action_id="step-1",
        reason="Search the repository first.",
        confidence=0.95,
    )

    result = policy.evaluate(
        decision=decision,
        context=make_context(),
    )

    assert result.status == "ALLOW"


def test_policy_requires_approval_for_low_confidence_action():
    policy = DecisionPolicy(
        min_execution_confidence=0.70,
    )

    decision = Decision(
        decision_type="EXECUTE_ACTION",
        action_id="step-1",
        reason="I am uncertain about this action.",
        confidence=0.40,
    )

    result = policy.evaluate(
        decision=decision,
        context=make_context(),
    )

    assert result.status == "REQUEST_APPROVAL"
    assert "below" in result.reason
    assert "0.70" in result.reason


def test_policy_requires_approval_for_high_risk_action():
    policy = DecisionPolicy()

    context = make_context()

    context.actions.append(
        Action(
            id="step-3",
            type="RUN_COMMAND",
            payload={"command": "pytest"},
        )
    )

    decision = Decision(
        decision_type="EXECUTE_ACTION",
        action_id="step-3",
        reason="Run the test suite.",
        confidence=0.99,
    )

    result = policy.evaluate(
        decision=decision,
        context=context,
    )

    assert result.status == "REQUEST_APPROVAL"
    assert "RUN_COMMAND" in result.reason


def test_policy_allows_replan():
    policy = DecisionPolicy()

    decision = Decision(
        decision_type="REPLAN",
        reason="The current approach failed.",
        confidence=0.80,
    )

    result = policy.evaluate(
        decision=decision,
        context=make_context(),
    )

    assert result.status == "ALLOW"


def test_policy_allows_abort():
    policy = DecisionPolicy()

    decision = Decision(
        decision_type="ABORT",
        reason="Continuing is unsafe.",
        confidence=1.0,
    )

    result = policy.evaluate(
        decision=decision,
        context=make_context(),
    )

    assert result.status == "ALLOW"


def test_policy_preserves_explicit_approval_request():
    policy = DecisionPolicy()

    decision = Decision(
        decision_type="REQUEST_APPROVAL",
        reason="Human authorization is needed.",
        confidence=0.90,
    )

    result = policy.evaluate(
        decision=decision,
        context=make_context(),
    )

    assert result.status == "REQUEST_APPROVAL"
    assert result.reason == "Human authorization is needed."


def test_policy_rejects_unknown_action_reference():
    policy = DecisionPolicy()

    decision = Decision(
        decision_type="EXECUTE_ACTION",
        action_id="does-not-exist",
        reason="Execute this.",
        confidence=0.95,
    )

    with pytest.raises(
        ValueError,
        match="unknown action",
    ):
        policy.evaluate(
            decision=decision,
            context=make_context(),
        )


def test_policy_rejects_invalid_confidence_threshold():
    with pytest.raises(
        ValueError,
        match="between 0.0 and 1.0",
    ):
        DecisionPolicy(
            min_execution_confidence=1.5,
        )
