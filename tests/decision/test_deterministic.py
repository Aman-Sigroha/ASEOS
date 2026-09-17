import pytest

from runtime.decision.deterministic import DeterministicDecisionEngine
from runtime.schemas.action import Action
from runtime.schemas.execution import ExecutionResult
from runtime.schemas.task import Task
from runtime.state.state import AgentState


def make_state(
    actions: list[Action] | None = None,
    execution_results: list[ExecutionResult] | None = None,
    status: str = "READY",
) -> AgentState:
    return AgentState(
        task=Task(
            id="task-001",
            description="Test task",
            workspace_path="/workspace",
        ),
        status=status,
        actions=actions or [],
        execution_results=execution_results or [],
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

    state = make_state(actions=actions)

    decision = await DeterministicDecisionEngine().decide(state)

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

    state = make_state(
        actions=actions,
        execution_results=execution_results,
    )

    decision = await DeterministicDecisionEngine().decide(state)

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

    state = make_state(
        actions=actions,
        execution_results=execution_results,
    )

    decision = await DeterministicDecisionEngine().decide(state)

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

    state = make_state(
        actions=actions,
        execution_results=execution_results,
    )

    decision = await DeterministicDecisionEngine().decide(state)

    assert decision.decision_type == "REPLAN"
    assert decision.action_id is None


@pytest.mark.asyncio
async def test_decision_engine_completes_without_actions():

    state = make_state()

    decision = await DeterministicDecisionEngine().decide(state)

    assert decision.decision_type == "COMPLETE"


@pytest.mark.asyncio
async def test_decision_engine_replans_failed_task():

    state = make_state(status="FAILED")

    decision = await DeterministicDecisionEngine().decide(state)

    assert decision.decision_type == "REPLAN"
