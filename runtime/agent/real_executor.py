from execution.gateway.dispatcher import ExecutionGateway
from execution.gateway.tools import DEFAULT_CAPABILITIES, default_tools
from runtime.agent.executor import ActionExecutor
from runtime.schemas.action import Action
from runtime.schemas.execution import ExecutionResult


class RealActionExecutor(ActionExecutor):
    """Execute supported actions inside a configured workspace.

    Thin adapter: every action goes through the ExecutionGateway, which
    resolves a registered tool, enforces capabilities, validates arguments
    and normalizes the result.
    """

    def __init__(
        self,
        workspace_path: str,
        gateway: ExecutionGateway | None = None,
    ) -> None:
        self.gateway = gateway or ExecutionGateway(
            workspace_path,
            tools=default_tools(),
            granted_capabilities=DEFAULT_CAPABILITIES,
        )
        self.workspace = self.gateway.workspace

    async def execute(self, action: Action) -> ExecutionResult:
        return await self.gateway.execute(action)
