import asyncio
import time
from pathlib import Path

from pydantic import ValidationError

from execution.gateway.tool import (
    Tool,
    ToolContext,
    ToolError,
    ToolOutput,
)
from runtime.schemas.action import Action
from runtime.schemas.execution import ExecutionResult


class ExecutionGateway:
    """Single validated path from an Action to a normalized ExecutionResult.

    Order: resolve registered tool -> check capabilities -> validate
    arguments -> run with a timeout -> normalize. The gateway never
    dispatches by model-supplied method names, only by registered tools.
    """

    def __init__(
        self,
        workspace_path: str,
        tools: list[Tool] | None = None,
        granted_capabilities: frozenset[str] | set[str] | None = None,
    ) -> None:
        self.workspace = Path(workspace_path).resolve()

        if not self.workspace.is_dir():
            raise ValueError(
                f"Workspace must be an existing directory: {self.workspace}"
            )

        self._tools: dict[str, Tool] = {}
        self.granted_capabilities = frozenset(granted_capabilities or ())

        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        action_type = tool.spec.action_type

        if action_type in self._tools:
            raise ValueError(
                f"A tool is already registered for '{action_type}'."
            )

        self._tools[action_type] = tool

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def get_tool(self, action_type: str) -> Tool | None:
        return self._tools.get(action_type)

    async def execute(self, action: Action) -> ExecutionResult:
        started = time.perf_counter()
        tool = self.get_tool(action.type)
        tool_name = tool.spec.name if tool is not None else None

        try:
            if tool is None:
                raise ToolError(
                    "UNKNOWN_TOOL",
                    f"Unsupported action type: {action.type}",
                )

            self._check_capabilities(tool)
            args = self._validate_args(tool, action.payload)
            output = await self._run(tool, args, action)
            return self._result(action, tool_name, started, output=output)

        except ToolError as error:
            return self._result(action, tool_name, started, error=error)

        except Exception as exc:  # unexpected tool bug: report, never crash
            return self._result(
                action,
                tool_name,
                started,
                error=ToolError(
                    "INTERNAL_ERROR", f"{type(exc).__name__}: {exc}"
                ),
            )

    def _check_capabilities(self, tool: Tool) -> None:
        missing = tool.spec.capabilities - self.granted_capabilities

        if missing:
            raise ToolError(
                "PERMISSION_DENIED",
                f"Tool '{tool.spec.name}' is disabled: capability "
                f"{sorted(missing)} has not been granted. It requires a "
                "separate, explicitly approved execution policy.",
            )

    @staticmethod
    def _validate_args(tool: Tool, payload: dict):
        try:
            return tool.spec.args_model.model_validate(payload)
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'payload'}: {e['msg']}"
                for e in exc.errors()
            )
            raise ToolError(
                "INVALID_ARGUMENTS",
                f"Invalid arguments for '{tool.spec.name}': {details}",
            ) from exc

    async def _run(self, tool: Tool, args, action: Action) -> ToolOutput:
        context = ToolContext(
            workspace=self.workspace,
            action_id=action.id,
            capabilities=self.granted_capabilities,
        )

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(tool.run, context, args),
                timeout=tool.spec.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            # The worker thread cannot be interrupted; tools that spawn
            # processes must enforce their own timeouts.
            raise ToolError(
                "TIMEOUT",
                f"Tool '{tool.spec.name}' exceeded "
                f"{tool.spec.timeout_seconds:g}s.",
            ) from exc

    @staticmethod
    def _result(
        action: Action,
        tool_name: str | None,
        started: float,
        output: ToolOutput | None = None,
        error: ToolError | None = None,
    ) -> ExecutionResult:
        duration_ms = int((time.perf_counter() - started) * 1000)

        if error is not None:
            return ExecutionResult(
                action_id=action.id,
                success=False,
                stdout=error.stdout,
                stderr=error.stderr,
                exit_code=error.exit_code,
                duration_ms=duration_ms,
                tool=tool_name,
                error_category=error.category,
                data=error.data,
            )

        assert output is not None
        return ExecutionResult(
            action_id=action.id,
            success=True,
            stdout=output.stdout,
            stderr=output.stderr,
            exit_code=output.exit_code,
            duration_ms=duration_ms,
            tool=tool_name,
            truncated=output.truncated,
            data=output.data,
        )
