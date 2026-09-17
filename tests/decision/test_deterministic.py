import pytest

from runtime.decision.deterministic import DeterministicDecisionEngine
from runtime.schemas.action import Action
from runtime.schemas.execution import ExecutionResult
from runtime.schemas.task import Task
from runtime.state.state import AgentState
from runtime.verification.result import VerificationResult
from runtime.decision.context import DecisionContext
from runtime.schemas.understanding import TaskUnderstanding


def make_context(
    actions: list[Action] | None = None,
    execution_results: list[ExecutionResult] | None = None,
    current_plan_results: list[ExecutionResult] | None = None,
    status: str = "READY",
) -> DecisionContext:
    understanding = TaskUnderstanding(
        goal="Fix calculator bug",
        expected_outcome="Calculator returns correct results",
    )

    return DecisionContext(
        task_id="task-001",
        task_description="Fix calculator bug",
        understanding=understanding,
        repository_summary="Python calculator project",
        plan_goal="Fix calculator bug",
        actions=actions or [],
        execution_results=execution_results or [],
        current_plan_results=current_plan_results or [],
        status=status,
    )


@pytest.mark.asyncio
async def test_decision_engine_executes_first_action():

    actions = [
        Action(
            id="action-001",
            type="SEARCH",
            payload={"query": "calculator"},
        ),
    ]

    context = make_context(actions=actions)

    decision = await DeterministicDecisionEngine().decide(context)

    assert decision.decision_type == "EXECUTE_ACTION"
    assert decision.action_id == "action-001"
    assert decision.confidence == 1.0


@pytest.mark.asyncio
async def test_decision_engine_executes_next_action():

    actions = [
        Action(
            id="action-001",
            type="SEARCH",
            payload={"query": "calculator"},
        ),
        Action(
            id="action-002",
            type="READ",
            payload={"path": "src/calculator.py"},
        ),
    ]

    execution_results = [
        ExecutionResult(
            action_id="action-001",
            success=True,
            exit_code=0,
        ),
    ]

    context = make_context(
        actions=actions,
        execution_results=execution_results,
        current_plan_results=execution_results,
    )

    decision = await DeterministicDecisionEngine().decide(context)

    assert decision.decision_type == "EXECUTE_ACTION"
    assert decision.action_id == "action-002"


@pytest.mark.asyncio
async def test_decision_engine_completes_when_all_actions_succeed():

    actions = [
        Action(
            id="action-001",
            type="SEARCH",
            payload={"query": "calculator"},
        ),
    ]

    execution_results = [
        ExecutionResult(
            action_id="action-001",
            success=True,
            exit_code=0,
        ),
    ]

    context = make_context(
        actions=actions,
        execution_results=execution_results,
        current_plan_results=execution_results,
    )

    decision = await DeterministicDecisionEngine().decide(context)

    assert decision.decision_type == "COMPLETE"
    assert decision.action_id is None


@pytest.mark.asyncio
async def test_decision_engine_requests_replan_after_failed_action():

    actions = [
        Action(
            id="action-001",
            type="SEARCH",
            payload={"query": "calculator"},
        ),
    ]

    execution_results = [
        ExecutionResult(
            action_id="action-001",
            success=False,
            stderr="Command failed",
            exit_code=1,
        ),
    ]

    context = make_context(
        actions=actions,
        execution_results=execution_results,
        current_plan_results=execution_results,
    )

    decision = await DeterministicDecisionEngine().decide(context)

    assert decision.decision_type == "REPLAN"
    assert decision.action_id is None


@pytest.mark.asyncio
async def test_decision_engine_completes_without_actions():

    context = make_context()

    decision = await DeterministicDecisionEngine().decide(context)

    assert decision.decision_type == "COMPLETE"


@pytest.mark.asyncio
async def test_decision_engine_replans_failed_task():

    context = make_context(status="FAILED")

    decision = await DeterministicDecisionEngine().decide(context)

    assert decision.decision_type == "REPLAN"


@pytest.mark.asyncio
async def test_decision_engine_replans_after_verification_failure():

    actions = [
        Action(
            id="action-001",
            type="EDIT",
            payload={"path": "src/calculator.py"},
        ),
    ]

    verification_result = VerificationResult(
        status="FAIL",
        summary="Unit test failed.",
    )

    context = make_context(
        actions=actions,
    )

    context.verification_result = verification_result

    decision = await DeterministicDecisionEngine().decide(context)

    assert decision.decision_type == "REPLAN"
    assert decision.action_id is None
