from runtime.agent.executor import ActionExecutor
from runtime.schemas.action import Action
from runtime.schemas.execution import ExecutionResult


class MockActionExecutor(ActionExecutor):
    """Deterministic executor used for agent-runtime tests."""

    def __init__(self) -> None:
        self.executed_actions: list[Action] = []

    async def execute(
        self,
        action: Action,
    ) -> ExecutionResult:
        self.executed_actions.append(action)

        return ExecutionResult(
            action_id=action.id,
            success=True,
            stdout=f"Executed {action.type}",
            stderr="",
            exit_code=0,
            duration_ms=1,
        )
