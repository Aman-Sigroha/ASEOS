import shlex
from typing import Literal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from execution.commands.process import ProcessLimits, ProcessResult, run_process
from execution.commands.policy import CAP_SHELL_NETWORK, CommandPolicy, PolicyDecision
from execution.security.levels import ExecutionLevel
from execution.testing.adapters import TargetError
from execution.testing.service import run_test_suite
from execution.versioning import workspace_version
from execution.gateway.capabilities import CAP_FS_READ, CAP_SHELL_RUN, CAP_TEST_RUN
from execution.security.levels import ExecutionLevel
from execution.gateway.tool import (
    Tool,
    ToolContext,
    ToolError,
    ToolOutput,
    ToolSpec,
    resolve_in_workspace,
)
from runtime.schemas.execution import ArtifactRef

_SHELL_OPERATORS = {"|", "||", "&", "&&", ";", ">", ">>", "<", "<<", "2>", "2>&1"}
_ERROR_KIND_CATEGORY = {
    "invalid_cwd": "INVALID_ARGUMENTS",
    "invalid_env": "INVALID_ARGUMENTS",
    "invalid_argv": "INVALID_ARGUMENTS",
    "unsafe_argument": "INVALID_ARGUMENTS",
    "permission": "PERMISSION_DENIED",
    "sandbox_unavailable": "PERMISSION_DENIED",
    "sandbox_refused": "PERMISSION_DENIED",
}


def resolve_sandbox(context: ToolContext):
    """The sandbox to use, or a refusal. Never silently downgrades to the host."""

    sandbox = context.sandbox

    if sandbox is None:
        if context.require_sandbox:
            raise ToolError(
                "PERMISSION_DENIED",
                "Commands must run in a sandbox but none is configured; "
                "refusing to run on the host.",
            )

        return None

    usable, reason = sandbox.availability()

    if not usable:
        raise ToolError(
            "PERMISSION_DENIED",
            f"The sandbox is unavailable ({reason}); refusing to run on the host.",
        )

    return sandbox


def _artifacts(result: ProcessResult) -> list[ArtifactRef]:
    return [
        ArtifactRef(
            id=f"{result.run_id}.{stream}",
            kind="command_output",
            description=path,
        )
        for stream, path in result.output_files.items()
    ]


def _data(
    result: ProcessResult,
    workspace: Path,
    decision: PolicyDecision | None = None,
) -> dict[str, Any]:
    try:
        cwd = Path(result.cwd).relative_to(workspace).as_posix() or "."
    except ValueError:
        cwd = result.cwd

    data: dict[str, Any] = {
        "run_id": result.run_id,
        "command": result.argv,
        "cwd": cwd,
        "status": result.status,
        "duration_ms": result.duration_ms,
        "stdout_bytes": result.stdout_total,
        "stderr_bytes": result.stderr_total,
        "stdout_truncated": result.stdout_truncated,
        "stderr_truncated": result.stderr_truncated,
        "spool_truncated": result.spool_truncated,
        "output_files": result.output_files,
        "process_tree": result.tree,
        "sandboxed": "sandbox" in result.tree,
    }

    if decision is not None:
        data["policy"] = {
            "rule": decision.rule,
            "category": decision.category,
            "risk": decision.risk,
        }

    return data


def to_output(
    result: ProcessResult,
    workspace: Path,
    *,
    failure_category: str,
    decision: PolicyDecision | None = None,
) -> ToolOutput:
    """Turn a process result into a ToolOutput, or raise a categorized ToolError."""

    data = _data(result, workspace, decision)
    artifacts = _artifacts(result)

    if result.status == "PASS":
        return ToolOutput(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            data=data,
            truncated=result.truncated,
            artifacts=artifacts,
        )

    if result.status == "FAIL":
        category, message = failure_category, (
            f"Command exited with code {result.exit_code}"
        )
    elif result.status == "TIMEOUT":
        category, message = "TIMEOUT", "Command timed out and was terminated"
    elif result.status == "CANCELLED":
        category, message = "CANCELLED", "Command was cancelled and terminated"
    else:
        category = _ERROR_KIND_CATEGORY.get(result.error_kind or "", "PROCESS_FAILURE")
        message = result.error or "Command could not be run"

    leftover = result.tree.get("leftover")

    if leftover:
        message += f" ({leftover} process(es) could not be stopped)"

    raise ToolError(
        category,  # type: ignore[arg-type]
        message,
        stdout=result.stdout,
        stderr=result.stderr or message,
        exit_code=result.exit_code,
        data=data,
        artifacts=artifacts,
    )


# ------------------------------------------------------------- shell.run


class RunCommandArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    command: str | list[str]
    cwd: str = "."
    timeout_seconds: float | None = Field(default=None, gt=0)
    env: dict[str, str] | None = None

    @field_validator("command")
    @classmethod
    def _not_empty(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("command must not be empty")

        if isinstance(value, list) and not value:
            raise ValueError("command must not be empty")

        return value


def split_command(command: str | list[str]) -> list[str]:
    """argv list from a list, or from a string split like a POSIX shell would
    (quoting only — nothing is ever executed by a shell)."""

    if isinstance(command, list):
        return command

    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        raise ToolError("INVALID_ARGUMENTS", f"Cannot parse command: {exc}") from exc

    if any(token in _SHELL_OPERATORS for token in argv):
        raise ToolError(
            "INVALID_ARGUMENTS",
            "Shell operators (pipes, redirects, &&, ;) are not supported; "
            "run one program with its arguments.",
        )

    return argv


class RunCommandTool(Tool):
    """Run one allowed program (argv, never a shell) under the command policy."""

    spec = ToolSpec(
        name="shell.run",
        level=ExecutionLevel.DEV_EXECUTION,
        action_type="RUN_COMMAND",
        description=(
            "Run one allowed development command (tests, linters, type "
            "checkers, builds) as an argv list or a plain command string. "
            "No shell: pipes, redirects and && are not supported. Output is "
            "bounded; the whole process tree is killed on timeout."
        ),
        args_model=RunCommandArgs,
        capabilities=frozenset({CAP_SHELL_RUN}),
        read_only=False,
        timeout_seconds=660,
    )

    def __init__(
        self,
        policy: CommandPolicy | None = None,
        limits: ProcessLimits | None = None,
    ) -> None:
        self.policy = policy or CommandPolicy.default()
        self.limits = limits or ProcessLimits()

    def run(self, context: ToolContext, args: RunCommandArgs) -> ToolOutput:
        argv = split_command(args.command)
        cwd = resolve_in_workspace(context.workspace, args.cwd)
        decision = self.policy.evaluate(
            argv, cwd, context.workspace, context.capabilities
        )

        if not decision.allowed:
            raise ToolError(
                "PERMISSION_DENIED",
                f"Command refused by policy: {decision.reason}",
                data={
                    "command": argv,
                    "policy": {
                        "rule": decision.rule,
                        "category": decision.category,
                        "risk": decision.risk,
                        "required_capabilities": sorted(
                            decision.required_capabilities
                        ),
                    },
                },
            )

        if decision.risk == "high" and context.max_level < ExecutionLevel.ELEVATED:
            raise ToolError(
                "PERMISSION_DENIED",
                "This command is high risk and needs execution level "
                f"ELEVATED; this gateway is limited to {context.max_level.name}.",
                data={"command": argv, "policy": {"rule": decision.rule, "risk": decision.risk}},
            )

        sandbox = resolve_sandbox(context)
        limits = ProcessLimits(
            timeout_seconds=self.policy.clamp_timeout(args.timeout_seconds),
            max_memory_bytes=self.limits.max_memory_bytes,
            max_processes=self.limits.max_processes,
            head_bytes=self.limits.head_bytes,
            tail_bytes=self.limits.tail_bytes,
            max_spool_bytes=self.limits.max_spool_bytes,
            kill_grace_seconds=self.limits.kill_grace_seconds,
        )
        result = run_process(
            argv,
            cwd,
            env_extra=args.env,
            limits=limits,
            cancel=context.cancel,
            workspace=context.workspace,
            sandbox=sandbox,
            network=bool(decision.required_capabilities & {CAP_SHELL_NETWORK}),
        )
        return to_output(
            result,
            context.workspace,
            failure_category="PROCESS_FAILURE",
            decision=decision,
        )


# -------------------------------------------------------------- test.run


class TestRunArgs(BaseModel):
    __test__ = False
    model_config = ConfigDict(extra="ignore")

    framework: Literal[
        "auto", "pytest", "unittest", "npm", "maven", "gradle", "cargo", "go"
    ] = "auto"
    targets: list[str] = Field(default_factory=list)
    keyword: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)


_STATUS_CATEGORY = {
    "FAILED": "TEST_FAILURE",
    "ERROR": "TEST_FAILURE",
    "NO_TESTS": "TEST_FAILURE",
    "UNVERIFIED": "TEST_FAILURE",
    "TIMEOUT": "TIMEOUT",
    "CANCELLED": "CANCELLED",
}
_NOT_RUN_CATEGORY = {
    "no_framework": "INVALID_ARGUMENTS",
    "policy": "PERMISSION_DENIED",
    "sandbox": "PERMISSION_DENIED",
}


class RunTestTool(Tool):
    __test__ = False
    spec = ToolSpec(
        name="test.run",
        level=ExecutionLevel.DEV_EXECUTION,
        action_type="RUN_TEST",
        description=(
            "Detect the project's test framework (pytest, unittest, npm, "
            "Maven, Gradle, Cargo, Go) and run the full suite or specific "
            "targets. Returns a normalized report: status, passed/failed/"
            "skipped counts, failing cases, and the exact workspace version "
            "that was tested. Success means every test passed; check "
            "data.status to tell failing tests from errors, no tests, "
            "and a command that could not start."
        ),
        args_model=TestRunArgs,
        capabilities=frozenset({CAP_TEST_RUN}),
        read_only=False,
        timeout_seconds=660,
    )

    def __init__(self, policy: CommandPolicy | None = None) -> None:
        self.policy = policy or CommandPolicy.default()

    def run(self, context: ToolContext, args: TestRunArgs) -> ToolOutput:
        sandbox = resolve_sandbox(context)

        try:
            report, process = run_test_suite(
                context.workspace,
                framework=args.framework,
                targets=args.targets,
                keyword=args.keyword,
                timeout=self.policy.clamp_timeout(args.timeout_seconds or 120.0),
                cancel=context.cancel,
                sandbox=sandbox,
                policy=self.policy,
                capabilities=context.capabilities,
            )
        except TargetError as exc:
            raise ToolError("INVALID_ARGUMENTS", str(exc)) from exc

        data = report.model_dump()
        data["process_tree"] = process.tree if process else {}
        data["sandboxed"] = bool(process and "sandbox" in process.tree)
        artifacts = _artifacts(process) if process else []
        stdout = process.stdout if process else ""
        stderr = process.stderr if process else ""

        if report.status == "PASSED":
            return ToolOutput(
                stdout=stdout,
                stderr=stderr,
                exit_code=report.exit_code,
                data=data,
                truncated=bool(process and process.truncated),
                artifacts=artifacts,
            )

        if report.status == "NOT_RUN":
            category = _NOT_RUN_CATEGORY.get(report.not_run_kind or "", "PROCESS_FAILURE")
        else:
            category = _STATUS_CATEGORY.get(report.status, "TEST_FAILURE")

        raise ToolError(
            category,  # type: ignore[arg-type]
            report.summary,
            stdout=stdout,
            stderr=stderr or report.summary,
            exit_code=report.exit_code,
            data=data,
            artifacts=artifacts,
        )


class TestFreshnessArgs(BaseModel):
    __test__ = False
    model_config = ConfigDict(extra="ignore")

    fingerprint: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


class TestFreshnessTool(Tool):
    __test__ = False
    spec = ToolSpec(
        name="test.freshness",
        level=ExecutionLevel.READ_ONLY,
        action_type="TEST_FRESHNESS",
        description=(
            "Tell whether a test result is still current: compares the "
            "workspace_version.fingerprint of an earlier test.run with the "
            "workspace as it is now. If the code changed, the old result is "
            "stale and the tests must be run again."
        ),
        args_model=TestFreshnessArgs,
        capabilities=frozenset({CAP_FS_READ}),
        read_only=True,
        idempotent=True,
        timeout_seconds=120,
    )

    def run(self, context: ToolContext, args: TestFreshnessArgs) -> ToolOutput:
        now = workspace_version(context.workspace)
        current = now["fingerprint"] == args.fingerprint.lower()
        return ToolOutput(
            stdout="current" if current else "stale: the workspace changed since that run",
            data={"current": current, "workspace_version": now},
        )
