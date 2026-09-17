import pytest

from runtime.agent.executor import ActionExecutor
from runtime.schemas.action import Action


def test_action_executor_is_abstract():
    with pytest.raises(TypeError):
        ActionExecutor()


def test_action_executor_requires_execute():
    class IncompleteExecutor(ActionExecutor):
        pass

    with pytest.raises(TypeError):
        IncompleteExecutor()


@pytest.mark.asyncio
async def test_action_executor_can_be_implemented():

    class FakeExecutor(ActionExecutor):
        async def execute(self, action):
            raise NotImplementedError

    executor = FakeExecutor()

    assert executor is not None
