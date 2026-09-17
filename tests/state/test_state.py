from runtime.schemas.task import Task
from runtime.state.state import AgentState


def test_agent_state_defaults():
    task = Task(
        id="task-001",
        description="Fix the login bug",
        workspace_path="C:/projects/example",
    )

    state = AgentState(task=task)

    assert state.task == task
    assert state.status == "CREATED"
    assert state.understanding is None
    assert state.repository_context is None
    assert state.plan is None
    assert state.actions == []
    assert state.execution_results == []
    assert state.current_action_index == 0


def test_agent_state_accepts_status_and_actions():
    task = Task(
        id="task-001",
        description="Fix the login bug",
        workspace_path="C:/projects/example",
    )

    state = AgentState(
        task=task,
        status="EXECUTING",
        current_action_index=2,
    )

    assert state.status == "EXECUTING"
    assert state.current_action_index == 2
