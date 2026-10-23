import asyncio

from execution.gateway.dispatcher import ExecutionGateway
from execution.gateway.tools import DEFAULT_CAPABILITIES, default_tools
from runtime.agent.executor import ActionExecutor
from runtime.decision.context_builder import DecisionContextBuilder
from runtime.decision.policy import DecisionPolicy
from runtime.schemas.action import Action
from runtime.schemas.execution import ExecutionResult
from runtime.tools.registry import ToolRegistry

# Pauses between attempts at a RETRY failure; its length is the retry budget.
DEFAULT_RETRY_DELAYS = (0.5, 1.5)


class RealActionExecutor(ActionExecutor):
    """Execute supported actions inside a configured workspace.

    Thin adapter: every action goes through the ExecutionGateway, which
    resolves a registered tool, enforces capabilities, validates arguments
    and normalizes the result.

    Retry versus replan: the gateway labels each failure with a disposition.
    Only RETRY failures (the action never started, or it is idempotent) are
    repeated here, a bounded number of times, with the attempts recorded in
    `result.data["attempts"]`. Everything else goes up to the runtime
    unchanged, so it decides whether to replan, ask a human or stop.
    """

    def __init__(
        self,
        workspace_path: str,
        gateway: ExecutionGateway | None = None,
        retry_delays: tuple[float, ...] = DEFAULT_RETRY_DELAYS,
    ) -> None:
        self.gateway = gateway or ExecutionGateway(
            workspace_path,
            tools=default_tools(),
            granted_capabilities=DEFAULT_CAPABILITIES,
        )
        self.workspace = self.gateway.workspace
        self.retry_delays = tuple(retry_delays)

    async def execute(self, action: Action) -> ExecutionResult:
        attempts = 1
        result = await self.gateway.execute(action)

        for delay in self.retry_delays:
            disposition = result.data.get("disposition") or {}

            if result.success or disposition.get("action") != "RETRY":
                break

            await asyncio.sleep(delay)
            attempts += 1
            result = await self.gateway.execute(action)

        if attempts > 1:
            result.data["attempts"] = attempts

        return result

    async def recover(self) -> dict | None:
        """Work an earlier run left unfinished, as a compact summary.

        Reports only: nothing is re-run, rolled back or deleted. The full
        report (with the file hashes a guarded retry needs) is
        `self.gateway.recover()`."""

        report = await asyncio.to_thread(self.gateway.recover)
        operations = report.get("operations")
        interrupted = operations.get("interrupted", []) if isinstance(operations, dict) else []
        leases = report.get("leases")
        stale = leases.get("stale", []) if isinstance(leases, dict) else []

        return {
            "needs_attention": bool(report.get("needs_attention")),
            "interrupted": [
                {
                    "op_id": item.get("op_id"),
                    "action_id": item.get("action_id"),
                    "tool": item.get("tool"),
                    "task_id": item.get("task_id"),
                    "verdict": (item.get("assessment") or {}).get("outcome"),
                    "resumable": (item.get("assessment") or {}).get("resumable"),
                    "recommendation": ((item.get("assessment") or {}).get("recommendation") or "")[:300],
                }
                for item in interrupted
            ][:50],
            "stale_leases": len(stale),
        }

    # ------------------------------------------------------- composition aids

    def tool_registry(self) -> ToolRegistry:
        """A registry of exactly what this gateway offers and has granted."""

        return ToolRegistry.from_tool_descriptions(self.gateway.describe_tools())

    def decision_policy(self, **kwargs) -> DecisionPolicy:
        """An autonomy policy that asks a human before any action type the
        gateway says needs approval."""

        return DecisionPolicy(
            high_risk_action_types=self.gateway.approval_required_action_types(),
            **kwargs,
        )

    def runtime_options(self, **policy_kwargs) -> dict:
        """Keyword arguments for `AgentRuntime` that make it use exactly this
        gateway's tools and approval rules. Built from the gateway as it is
        now: after a `grant`, build them again."""

        return {
            "decision_context_builder": DecisionContextBuilder(
                tool_registry=self.tool_registry()
            ),
            "decision_policy": self.decision_policy(**policy_kwargs),
        }

    def grant(self, capabilities: set[str]) -> frozenset[str]:
        """Record an approval (called by the runtime/UI, never by a model)."""

        return self.gateway.grant(capabilities)
