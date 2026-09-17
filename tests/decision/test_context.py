import pytest

from runtime.decision.context import DecisionContext
from runtime.schemas.action import Action
from runtime.schemas.execution import ExecutionResult
from runtime.schemas.task import Task
from runtime.schemas.understanding import TaskUnderstanding


def test_decision_context_creation():

    understanding = TaskUnderstanding(
        goal="Fix calculator bug",
        expected_outcome="Calculator returns correct results",
    )

    context = DecisionContext(
        task_id="task-001",
        task_description="Fix calculator bug",
        understanding=understanding,
        plan_goal="Fix calculator bug",
        status="READY",
    )

    assert context.task_id == "task-001"
    assert context.task_description == "Fix calculator bug"
    assert context.understanding.goal == "Fix calculator bug"
    assert context.plan_goal == "Fix calculator bug"
    assert context.current_action_index == 0
    assert context.execution_results == []
    assert context.current_plan_results == []
    assert context.verification_result is None
    assert context.replan_count == 0


def test_decision_context_preserves_execution_evidence():

    understanding = TaskUnderstanding(
        goal="Fix calculator bug",
        expected_outcome="Correct results",
    )

    result = ExecutionResult(
        action_id="action-001",
        success=False,
        stderr="Command failed",
        exit_code=1,
    )

    context = DecisionContext(
        task_id="task-001",
        task_description="Fix calculator bug",
        understanding=understanding,
        plan_goal="Fix calculator bug",
        execution_results=[result],
        current_plan_results=[result],
        status="EXECUTING",
    )

    assert len(context.execution_results) == 1
    assert context.execution_results[0].action_id == "action-001"
    assert context.execution_results[0].success is False


def test_decision_context_preserves_actions():

    understanding = TaskUnderstanding(
        goal="Fix calculator bug",
        expected_outcome="Correct results",
    )

    actions = [
        Action(
            id="action-001",
            type="SEARCH",
            payload={"query": "divide"},
        ),
        Action(
            id="action-002",
            type="READ",
            payload={"path": "src/calculator.py"},
        ),
    ]

    context = DecisionContext(
        task_id="task-001",
        task_description="Fix calculator bug",
        understanding=understanding,
        plan_goal="Fix calculator bug",
        actions=actions,
        current_action_index=1,
        status="EXECUTING",
    )

    assert len(context.actions) == 2
    assert context.actions[0].id == "action-001"
    assert context.current_action_index == 1


def test_decision_context_rejects_empty_task_id():

    understanding = TaskUnderstanding(
        goal="Fix calculator bug",
        expected_outcome="Correct results",
    )

    with pytest.raises(ValueError):
        DecisionContext(
            task_id="",
            task_description="Fix calculator bug",
            understanding=understanding,
            plan_goal="Fix calculator bug",
            status="READY",
        )
