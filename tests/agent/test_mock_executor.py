import pytest

from runtime.agent.mock_executor import MockActionExecutor
from runtime.schemas.action import Action


@pytest.mark.asyncio
async def test_mock_executor_executes_action():
    executor = MockActionExecutor()

    action = Action(
        id="action-001",
        type="SEARCH",
        payload={
            "query": "timeout",
        },
    )

    result = await executor.execute(action)

    assert result.action_id == "action-001"
    assert result.success is True
    assert result.exit_code == 0
    assert result.stdout == "Executed SEARCH"

    assert len(executor.executed_actions) == 1
    assert executor.executed_actions[0] == action
