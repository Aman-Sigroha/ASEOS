import pytest

from runtime.decision.context_builder import DecisionContextBuilder
from runtime.schemas.action import Action
from runtime.schemas.execution import ExecutionResult
from runtime.schemas.plan import Plan, PlanStep
from runtime.schemas.repository import RepositoryContext
from runtime.schemas.task import Task
from runtime.schemas.understanding import TaskUnderstanding
from runtime.state.state import AgentState
from runtime.verification.result import (
    VerificationCheck,
    VerificationResult,
)


def make_state() -> AgentState:
    task = Task(
        id="task-001",
        description="Fix calculator bug",
        workspace_path="/workspace",
    )

    understanding = TaskUnderstanding(
        goal="Fix calculator bug",
        expected_outcome="Calculator returns correct results",
        constraints=["Do not break existing behavior"],
        verification_requirements=["Run calculator tests"],
    )

    repository_context = RepositoryContext(
        root="/workspace",
        summary="Python calculator project",
    )

    plan = Plan(
        task_id="task-001",
        goal="Fix calculator bug",
        steps=[
            PlanStep(
                id="step-1",
                description="Search calculator",
                action_type="SEARCH",
                parameters={"query": "divide"},
            ),
        ],
    )

    return AgentState(
        task=task,
        status="EXECUTING",
        understanding=understanding,
        repository_context=repository_context,
        plan=plan,
        actions=[
            Action(
                id="step-1",
                type="SEARCH",
                payload={"query": "divide"},
            )
        ],
        current_action_index=0,
        execution_results=[
            ExecutionResult(
                action_id="step-1",
                success=True,
                exit_code=0,
            )
        ],
        current_plan_results=[
            ExecutionResult(
                action_id="step-1",
                success=True,
                exit_code=0,
            )
        ],
        verification_result=VerificationResult(
            status="FAIL",
            checks=[
                VerificationCheck(
                    name="unit-tests",
                    status="FAIL",
                    message="test failed",
                )
            ],
            summary="Verification failed.",
        ),
        replan_count=1,
    )


def test_context_builder_maps_agent_state():

    state = make_state()

    context = DecisionContextBuilder().build(state)

    assert context.task_id == state.task.id
    assert context.task_description == state.task.description

    assert context.understanding == state.understanding

    assert context.repository_summary == state.repository_context.summary

    assert context.plan_goal == state.plan.goal

    assert context.actions == state.actions
    assert context.current_action_index == 0

    assert context.execution_results == state.execution_results
    assert context.current_plan_results == state.current_plan_results

    assert context.verification_result == state.verification_result
    assert context.replan_count == 1
    assert context.status == "EXECUTING"


def test_context_builder_copies_lists():

    state = make_state()

    context = DecisionContextBuilder().build(state)

    context.actions.clear()
    context.execution_results.clear()
    context.current_plan_results.clear()

    assert len(state.actions) == 1
    assert len(state.execution_results) == 1
    assert len(state.current_plan_results) == 1


def test_context_builder_requires_understanding():

    state = make_state()
    state.understanding = None

    with pytest.raises(ValueError, match="understanding"):
        DecisionContextBuilder().build(state)


def test_context_builder_requires_plan():

    state = make_state()
    state.plan = None

    with pytest.raises(ValueError, match="plan"):
        DecisionContextBuilder().build(state)


def test_context_builder_requires_repository_context():

    state = make_state()
    state.repository_context = None

    with pytest.raises(ValueError, match="repository context"):
        DecisionContextBuilder().build(state)
