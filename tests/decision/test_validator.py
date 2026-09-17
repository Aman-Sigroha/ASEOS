import pytest

from runtime.decision.context import DecisionContext
from runtime.decision.decision import Decision
from runtime.decision.validator import (
    DecisionValidationError,
    DecisionValidator,
)
from runtime.schemas.action import Action
from runtime.schemas.execution import ExecutionResult
from runtime.schemas.understanding import TaskUnderstanding


def make_context(
    current_plan_results=None,
    replan_count=0,
) -> DecisionContext:
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
        current_plan_results=current_plan_results or [],
        verification_result=None,
        replan_count=replan_count,
        status="EXECUTING",
    )


def make_result(
    action_id: str,
    success: bool = True,
) -> ExecutionResult:
    return ExecutionResult(
        action_id=action_id,
        success=success,
        exit_code=0 if success else 1,
    )


def test_validator_accepts_valid_execute_action():
    validator = DecisionValidator()
    context = make_context()

    decision = Decision(
        decision_type="EXECUTE_ACTION",
        action_id="step-1",
        reason="Execute the first planned action.",
        confidence=0.95,
    )

    validator.validate(decision, context)


def test_validator_rejects_execute_without_action_id():
    validator = DecisionValidator()
    context = make_context()

    decision = Decision(
        decision_type="EXECUTE_ACTION",
        reason="Execute the next action.",
        confidence=0.95,
    )

    with pytest.raises(
        DecisionValidationError,
        match="must contain an action_id",
    ):
        validator.validate(decision, context)


def test_validator_rejects_unknown_action():
    validator = DecisionValidator()
    context = make_context()

    decision = Decision(
        decision_type="EXECUTE_ACTION",
        action_id="unknown-action",
        reason="Execute this action.",
        confidence=0.95,
    )

    with pytest.raises(
        DecisionValidationError,
        match="next valid action",
    ):
        validator.validate(decision, context)


def test_validator_rejects_out_of_order_action():
    validator = DecisionValidator()
    context = make_context(
        current_plan_results=[
            make_result("step-1"),
        ]
    )

    decision = Decision(
        decision_type="EXECUTE_ACTION",
        action_id="step-1",
        reason="Repeat the first action.",
        confidence=0.95,
    )

    with pytest.raises(
        DecisionValidationError,
        match="next valid action",
    ):
        validator.validate(decision, context)


def test_validator_rejects_execute_after_failed_action():
    validator = DecisionValidator()
    context = make_context(
        current_plan_results=[
            make_result("step-1", success=False),
        ]
    )

    decision = Decision(
        decision_type="EXECUTE_ACTION",
        action_id="step-2",
        reason="Continue execution.",
        confidence=0.95,
    )

    with pytest.raises(
        DecisionValidationError,
        match="failed action",
    ):
        validator.validate(decision, context)


def test_validator_accepts_complete_after_all_actions_succeed():
    validator = DecisionValidator()

    context = make_context(
        current_plan_results=[
            make_result("step-1"),
            make_result("step-2"),
        ]
    )

    decision = Decision(
        decision_type="COMPLETE",
        reason="All planned actions succeeded.",
        confidence=0.99,
    )

    validator.validate(decision, context)


def test_validator_rejects_premature_complete():
    validator = DecisionValidator()

    context = make_context(
        current_plan_results=[
            make_result("step-1"),
        ]
    )

    decision = Decision(
        decision_type="COMPLETE",
        reason="The task is complete.",
        confidence=0.99,
    )

    with pytest.raises(
        DecisionValidationError,
        match="unexecuted actions remain",
    ):
        validator.validate(decision, context)


def test_validator_accepts_replan_before_limit():
    validator = DecisionValidator()

    context = make_context(replan_count=1)

    decision = Decision(
        decision_type="REPLAN",
        reason="The current approach should be changed.",
        confidence=0.90,
    )

    validator.validate(
        decision,
        context,
        max_replans=3,
    )


def test_validator_rejects_replan_at_limit():
    validator = DecisionValidator()

    context = make_context(replan_count=3)

    decision = Decision(
        decision_type="REPLAN",
        reason="Try another approach.",
        confidence=0.90,
    )

    with pytest.raises(
        DecisionValidationError,
        match="maximum number of replans",
    ):
        validator.validate(
            decision,
            context,
            max_replans=3,
        )


def test_validator_accepts_abort():
    validator = DecisionValidator()
    context = make_context()

    decision = Decision(
        decision_type="ABORT",
        reason="Continuing is unsafe.",
        confidence=1.0,
    )

    validator.validate(decision, context)


def test_validator_accepts_request_approval():
    validator = DecisionValidator()
    context = make_context()

    decision = Decision(
        decision_type="REQUEST_APPROVAL",
        reason="Human approval is required.",
        confidence=0.98,
    )

    validator.validate(decision, context)
