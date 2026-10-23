import asyncio
import threading
import time
import uuid
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
from execution.git.repo import GitError, run_git
from execution.recovery.lease import LeaseConflict, LeaseLost, WorkspaceLease
from execution.recovery.locations import state_dir_for
from execution.recovery.ops import JournalError, NullOperation, OperationsLog
from execution.security.levels import PROFILES, ExecutionLevel, Profile
from execution.security.paths import MANAGED_AREA
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
        lease: bool | None = None,
        journal: bool = True,
        require_journal: bool = False,
        state_dir: Path | str | None = None,
        owner_id: str | None = None,
        mutation_wait_seconds: float = 60.0,
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
        # Concurrency and recovery. `lease=None` means: an exclusive write
        # lease for managed worktrees (one task owns one worktree), none
        # elsewhere; True/False force it.
        self.owner_id = owner_id or f"gw-{uuid.uuid4().hex[:12]}"
        self.mutation_wait_seconds = mutation_wait_seconds
        self.journal_enabled = journal
        self.require_journal = require_journal
        self._state_dir_arg = Path(state_dir) if state_dir is not None else None
        self._state_dir: Path | None = None
        self._lease_setting = lease
        self._lease: WorkspaceLease | None = None
        self._ops: OperationsLog | None = None
        self._mutation_locks: dict[int, asyncio.Lock] = {}

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

    @property
    def state_dir(self) -> Path:
        """Where leases and the operations journal live (opened on first use)."""

        if self._state_dir is None:
            self._state_dir = self._state_dir_arg or state_dir_for(self.workspace)

        return self._state_dir

    @property
    def lease_enabled(self) -> bool:
        if self._lease_setting is not None:
            return self._lease_setting

        return MANAGED_AREA in {part.lower() for part in self.workspace.parts}

    @property
    def operations(self) -> OperationsLog | None:
        if not self.journal_enabled:
            return None

        if self._ops is None:
            self._ops = OperationsLog(self.state_dir)

        return self._ops

    def release_lease(self) -> None:
        if self._lease is not None:
            self._lease.release()

    def _ensure_lease(self) -> None:
        if not self.lease_enabled:
            return

        if self._lease is None:
            self._lease = WorkspaceLease(
                self.state_dir, self.workspace, owner_id=self.owner_id, task_id=self.task_id
            )

        try:
            self._lease.acquire()
        except LeaseConflict as exc:
            holder = exc.holder
            raise ToolError(
                "WORKSPACE_UNAVAILABLE",
                str(exc),
                data={
                    "lease_conflict": True,
                    "holder": {
                        k: holder.get(k)
                        for k in ("owner_id", "pid", "host", "task_id", "heartbeat_age_seconds")
                    },
                },
            ) from exc
        except LeaseLost as exc:
            raise ToolError("WORKSPACE_UNAVAILABLE", str(exc), data={"lease_lost": True}) from exc
        except OSError as exc:
            raise ToolError(
                "WORKSPACE_UNAVAILABLE", f"Cannot take the workspace lease: {exc}"
            ) from exc

    def recover(self, *, terminate_orphans: bool = False) -> dict[str, Any]:
        """Inspect (and only report on) what an earlier crash or interruption
        left behind for this workspace.

        Sections: `operations` (journaled actions classified against the real
        files/HEAD/processes; interrupted ones are marked, never re-run),
        `leases` (stale write leases), `artifacts` (orphaned or missing
        blobs), `worktrees` (missing or unrecorded worktrees, when this
        workspace is a repository root). With `terminate_orphans`, a child
        process of a dead owner is stopped, but only when its identity (pid +
        creation time) is verified. Nothing is deleted or re-executed.
        """

        from execution.recovery.lease import all_leases, read_lease
        from execution.recovery.reconcile import reconcile_operations

        report: dict[str, Any] = {"workspace": str(self.workspace)}

        def section(name: str, produce: Callable[[], Any]) -> None:
            try:
                report[name] = produce()
            except Exception as exc:  # one failing section must not hide the others
                report[name] = {"error": f"{type(exc).__name__}: {exc}"}

        store = self.artifact_store

        def operations() -> Any:
            log = self.operations

            if log is None:
                return {"enabled": False}

            finished = None

            if store is not None:
                finished = {
                    m.action_id
                    for m in store.find(workspace=self.workspace_id, kind="execution_record", limit=100_000)
                    if m.action_id
                }

            return reconcile_operations(
                log,
                self.workspace,
                self_owner_id=self.owner_id,
                finished_actions=finished,
                terminate_orphans=terminate_orphans,
            )

        def leases() -> Any:
            return {
                "this_workspace": read_lease(self.state_dir, self.workspace),
                "stale": [lease for lease in all_leases(self.state_dir) if lease["stale"]],
            }

        def worktrees() -> Any:
            from execution.worktrees.manager import WorktreeManager

            if MANAGED_AREA in {part.lower() for part in self.workspace.parts}:
                return {"skipped": "inside a managed worktree"}

            return WorktreeManager(self.workspace).reconcile()

        section("operations", operations)
        section("leases", leases)
        section("artifacts", lambda: store.audit() if store else {"enabled": False})
        section("worktrees", worktrees)
        interrupted = report.get("operations", {}).get("interrupted", []) if isinstance(report.get("operations"), dict) else []
        report["needs_attention"] = bool(interrupted)
        return report

    def _mutation_lock(self) -> asyncio.Lock:
        key = id(asyncio.get_running_loop())
        return self._mutation_locks.setdefault(key, asyncio.Lock())

    def _head_sha(self) -> str | None:
        try:
            return run_git(self.workspace, ["rev-parse", "HEAD"], timeout=10).stdout.strip() or None
        except (GitError, OSError):
            return None

    async def _run_mutating(
        self, tool: Tool, args, action: Action, sink: ArtifactSink | NullSink
    ) -> ToolOutput:
        """One state-changing action at a time per workspace, under the
        workspace lease, with its intent journaled before it starts."""

        lock = self._mutation_lock() if tool.spec.serialize else None

        if lock is not None:
            try:
                await asyncio.wait_for(lock.acquire(), self.mutation_wait_seconds)
            except asyncio.TimeoutError:
                raise ToolError(
                    "TIMEOUT",
                    f"Waited {self.mutation_wait_seconds:g}s for another change to this "
                    "workspace to finish.",
                ) from None

        try:
            self._ensure_lease()
            handle: Any = NullOperation()
            log = self.operations

            if log is not None:
                head = await asyncio.to_thread(self._head_sha)
                digest, summary = summarize_args(action.payload)

                try:
                    handle = log.begin(
                        action_id=action.id,
                        action_type=action.type,
                        tool=tool.spec.name,
                        workspace=self.workspace,
                        owner_id=self.owner_id,
                        task_id=self.task_id,
                        args_sha256=digest,
                        args=summary,
                        head_before=head,
                    )
                except JournalError as exc:
                    if self.require_journal:
                        raise ToolError(
                            "RESOURCE_LIMIT",
                            f"{exc}; refusing to change the workspace without a recorded intent.",
                            data={"journal_error": str(exc)},
                        ) from exc

                    sink.errors.append(f"journal: {exc}")

            try:
                output = await self._run(tool, args, action, sink, handle)
            except ToolError as error:
                if handle.errors:
                    error.data = {**error.data, "journal_errors": list(handle.errors)}

                raise
            finally:
                handle.finish()

            if handle.errors:
                output.data["journal_errors"] = list(handle.errors)

            return output
        finally:
            if lock is not None:
                lock.release()

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
            if tool.spec.read_only:
                output = await self._run(tool, args, action, sink)
            else:
                output = await self._run_mutating(tool, args, action, sink)

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
        self,
        tool: Tool,
        args,
        action: Action,
        sink: ArtifactSink | NullSink,
        operation: Any = None,
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
            operation=operation if operation is not None else NullOperation(),
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

        stopped = not self.running()

        if stopped:
            self.release_lease()  # a task that is still running keeps its lease

        return stopped

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
