import asyncio
import threading
import time
from pathlib import Path
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from execution.artifacts.consistency import check_result
from execution.artifacts.records import (
    ExecutionRecord,
    links_from,
    now_iso,
    summarize_args,
)
from execution.artifacts.redact import redact_text
from execution.artifacts.sink import ArtifactSink, NullSink
from execution.artifacts.store import (
    ArtifactError,
    ArtifactStore,
    default_store_for,
    workspace_id,
)
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
        artifact_store: ArtifactStore | bool | None = None,
        task_id: str | None = None,
        record_reads: bool = False,
    ) -> None:
        self.workspace = Path(workspace_path).resolve()
        self.task_id = task_id
        self.workspace_id = workspace_id(self.workspace)
        self.record_reads = record_reads
        self.observers: list[Callable[[dict[str, Any]], None]] = []
        # None = open the default per-repository store on first use;
        # False = artifacts disabled; or an explicit store.
        self._store_setting = artifact_store
        self._store: ArtifactStore | None = (
            artifact_store if isinstance(artifact_store, ArtifactStore) else None
        )
        self.store_error: str | None = None

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

    @property
    def artifact_store(self) -> ArtifactStore | None:
        """The artifact store, opened on first use (None when disabled/unavailable)."""

        if self._store is None and self._store_setting is not False and self.store_error is None:
            try:
                self._store = default_store_for(self.workspace)
            except (ArtifactError, OSError) as exc:
                self.store_error = str(exc)

        return self._store

    def _sink(self, action: Action, tool_name: str | None) -> ArtifactSink | NullSink:
        store = self.artifact_store

        if store is None:
            return NullSink()

        return ArtifactSink(
            store,
            workspace=self.workspace_id,
            action_id=action.id,
            tool=tool_name,
            task_id=self.task_id,
        )

    def add_observer(self, observer: Callable[[dict[str, Any]], None]) -> None:
        """Called with each execution record (a dict) after the action ends.
        Lets the runtime forward records to its event system."""

        self.observers.append(observer)

    def check_evidence(self, result: ExecutionResult) -> list[str]:
        """Inconsistencies between a result and its own artifacts (see
        execution/artifacts/consistency.py); empty when consistent."""

        store = self.artifact_store
        sink: ArtifactSink | NullSink = (
            ArtifactSink(store, workspace=self.workspace_id) if store else NullSink()
        )
        return check_result(result, sink)

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
        started_at = now_iso()
        tool = self.get_tool(action.type)
        tool_name = tool.spec.name if tool is not None else None
        sink = self._sink(action, tool_name)

        try:
            if tool is None:
                raise ToolError(
                    "UNKNOWN_TOOL",
                    f"Unsupported action type: {action.type}",
                )

            self._check_capabilities(tool)
            args = self._validate_args(tool, action.payload)
            output = await self._run(tool, args, action, sink)
            result = self._result(action, tool_name, started, output=output)

        except ToolError as error:
            result = self._result(action, tool_name, started, error=error)

        except Exception as exc:  # unexpected tool bug: report, never crash
            result = self._result(
                action,
                tool_name,
                started,
                error=ToolError(
                    "INTERNAL_ERROR", f"{type(exc).__name__}: {exc}"
                ),
            )

        return self._finish(action, tool, result, sink, started_at)

    # ------------------------------------------------- redaction and records

    @staticmethod
    def _redact_value(value: Any) -> tuple[Any, int]:
        if isinstance(value, str):
            redacted = redact_text(value)
            return redacted.text, redacted.count

        if isinstance(value, dict):
            total = 0
            out = {}

            for key, item in value.items():
                out[key], count = ExecutionGateway._redact_value(item)
                total += count

            return out, total

        if isinstance(value, list):
            total = 0
            items = []

            for item in value:
                new, count = ExecutionGateway._redact_value(item)
                items.append(new)
                total += count

            return items, total

        return value, 0

    def _finish(
        self,
        action: Action,
        tool: Tool | None,
        result: ExecutionResult,
        sink: ArtifactSink | NullSink,
        started_at: str,
    ) -> ExecutionResult:
        """Mask secrets in program output, surface artifact failures, and
        write the execution record."""

        redactions = sum(ref.redactions for ref in result.artifacts)

        if tool is not None and tool.spec.redact_output:
            for field_name in ("stdout", "stderr"):
                masked = redact_text(getattr(result, field_name))
                setattr(result, field_name, masked.text)
                redactions += masked.count

            result.data, count = self._redact_value(result.data)
            redactions += count

        result.data.update(sink.failure_data())

        if tool is None or not (self.record_reads or not tool.spec.read_only):
            return result

        try:
            digest, args = summarize_args(action.payload)
            record = ExecutionRecord(
                action_id=action.id,
                action_type=action.type,
                tool=tool.spec.name,
                workspace_id=self.workspace_id,
                task_id=self.task_id,
                started_at=started_at,
                finished_at=now_iso(),
                duration_ms=result.duration_ms or 0,
                level=tool.spec.level.name,
                capabilities=sorted(tool.spec.capabilities),
                sandboxed=result.data.get("sandboxed"),
                success=result.success,
                error_category=result.error_category,
                exit_code=result.exit_code,
                truncated=result.truncated,
                args_sha256=digest,
                args=args,
                links=links_from(result.data),
                artifact_ids=[ref.id for ref in result.artifacts],
                artifact_errors=list(sink.errors),
                redactions=redactions,
            )
        except Exception:  # an audit bug must not change the outcome
            return result

        ref = sink.put(
            record.model_dump_json(),
            kind="execution_record",
            description=f"{tool.spec.name} {action.id}",
            media_type="application/json",
            retention="audit",
            redact=False,  # built from summarized, already-redacted fields
        )

        if ref is not None:
            result.data["execution_record"] = ref.id  # outputs stay in `artifacts`

        result.data.update(sink.failure_data())  # includes a failed audit write

        payload = record.model_dump()

        for observer in list(self.observers):
            try:
                observer(payload)
            except Exception:
                pass  # observers are best-effort

        return result

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

    async def _run(
        self, tool: Tool, args, action: Action, sink: ArtifactSink | NullSink
    ) -> ToolOutput:
        cancel = threading.Event()
        context = ToolContext(
            workspace=self.workspace,
            action_id=action.id,
            capabilities=self.granted_capabilities,
            max_level=self.max_level,
            sandbox=self.sandbox,
            require_sandbox=self.require_sandbox,
            cancel=cancel,
            artifacts=sink,
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
