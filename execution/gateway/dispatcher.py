import asyncio
import threading
import time
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from execution.security.levels import PROFILES, ExecutionLevel, Profile
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
        stop_grace_seconds: float = 10.0,
        profile: Profile | str | None = None,
        sandbox: Any = None,
        require_sandbox: bool | None = None,
    ) -> None:
        self.workspace = Path(workspace_path).resolve()

        if not self.workspace.is_dir():
            raise ValueError(
                f"Workspace must be an existing directory: {self.workspace}"
            )

        self._tools: dict[str, Tool] = {}
        self.stop_grace_seconds = stop_grace_seconds
        self._running: dict[str, list[threading.Event]] = {}
        self._running_lock = threading.Lock()
        if isinstance(profile, str):
            if profile not in PROFILES:
                raise ValueError(f"Unknown profile: {profile!r}")

            profile = PROFILES[profile]

        self.profile = profile
        capabilities = (
            granted_capabilities
            if granted_capabilities is not None
            else (profile.capabilities if profile else ())
        )
        # A profile is a ceiling: capabilities beyond it are never granted.
        self.granted_capabilities = frozenset(capabilities)

        if profile is not None:
            self.granted_capabilities &= profile.capabilities

        self.max_level = (
            profile.max_level if profile else ExecutionLevel.ELEVATED
        )
        self.sandbox = sandbox
        self.require_sandbox = (
            require_sandbox
            if require_sandbox is not None
            else bool(profile and profile.sandbox_required)
        )

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

    def describe_tools(self) -> list[dict[str, Any]]:
        """What each registered tool needs, for planners and UIs."""

        return [
            {
                "name": tool.spec.name,
                "action_type": tool.spec.action_type,
                "level": tool.spec.level.name,
                "capabilities": sorted(tool.spec.capabilities),
                "read_only": tool.spec.read_only,
                "idempotent": tool.spec.idempotent,
                "timeout_seconds": tool.spec.timeout_seconds,
                "available": self._permitted(tool),
            }
            for tool in self._tools.values()
        ]

    def _permitted(self, tool: Tool) -> bool:
        return (
            tool.spec.level <= self.max_level
            and not (tool.spec.capabilities - self.granted_capabilities)
        )

    def _check_capabilities(self, tool: Tool) -> None:
        if tool.spec.level > self.max_level:
            raise ToolError(
                "PERMISSION_DENIED",
                f"Tool '{tool.spec.name}' is disabled: it needs execution "
                f"level {tool.spec.level.name}, but this gateway is limited "
                f"to {self.max_level.name}.",
            )

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
        cancel = threading.Event()
        context = ToolContext(
            workspace=self.workspace,
            action_id=action.id,
            capabilities=self.granted_capabilities,
            max_level=self.max_level,
            sandbox=self.sandbox,
            require_sandbox=self.require_sandbox,
            cancel=cancel,
        )
        self._track(action.id, cancel)
        future = asyncio.ensure_future(
            asyncio.to_thread(tool.run, context, args)
        )

        try:
            done, _ = await asyncio.wait(
                {future}, timeout=tool.spec.timeout_seconds
            )

            if future in done:
                return future.result()

            # Ask the tool to stop (it kills any process tree it started),
            # then collect whatever it managed to produce.
            cancel.set()
            done, _ = await asyncio.wait(
                {future}, timeout=self.stop_grace_seconds
            )
            message = (
                f"Tool '{tool.spec.name}' exceeded "
                f"{tool.spec.timeout_seconds:g}s."
            )

            if future in done:
                error = future.exception()

                if error is None:
                    late = future.result()
                    raise ToolError(
                        "TIMEOUT",
                        message + " It finished after the deadline.",
                        stdout=late.stdout,
                        stderr=late.stderr or message,
                        exit_code=late.exit_code,
                        data=late.data,
                    )

                if isinstance(error, ToolError):
                    raise ToolError(
                        "TIMEOUT",
                        message,
                        stdout=error.stdout,
                        stderr=error.stderr or message,
                        exit_code=error.exit_code,
                        data=error.data,
                    ) from error

                raise error

            raise ToolError(
                "TIMEOUT",
                message + " It did not stop when asked; its worker thread "
                "may still be running.",
            )
        except asyncio.CancelledError:
            cancel.set()
            await asyncio.wait({future}, timeout=self.stop_grace_seconds)
            raise
        finally:
            self._untrack(action.id, cancel)

    # ------------------------------------------------ cancellation / shutdown

    def _track(self, action_id: str, event: threading.Event) -> None:
        with self._running_lock:
            self._running.setdefault(action_id, []).append(event)

    def _untrack(self, action_id: str, event: threading.Event) -> None:
        with self._running_lock:
            events = self._running.get(action_id, [])

            if event in events:
                events.remove(event)

            if not events:
                self._running.pop(action_id, None)

    def running(self) -> list[str]:
        """Ids of actions currently executing."""

        with self._running_lock:
            return list(self._running)

    def cancel(self, action_id: str) -> bool:
        """Ask a running action to stop. Returns False if it is not running.

        Safe to call from any thread. The action's result then reports
        `error_category="CANCELLED"` with whatever output it had produced.
        """

        with self._running_lock:
            events = list(self._running.get(action_id, []))

        for event in events:
            event.set()

        return bool(events)

    def cancel_all(self) -> int:
        with self._running_lock:
            events = [e for group in self._running.values() for e in group]

        for event in events:
            event.set()

        return len(events)

    async def shutdown(self, timeout: float = 10.0) -> bool:
        """Cancel everything and wait for it to finish. True if all stopped."""

        self.cancel_all()
        deadline = time.monotonic() + timeout

        while self.running() and time.monotonic() < deadline:
            await asyncio.sleep(0.05)

        return not self.running()

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
                artifacts=error.artifacts,
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
            artifacts=output.artifacts,
        )
