import shlex
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from execution.commands.process import ProcessLimits, ProcessResult, run_process
from execution.commands.policy import CommandPolicy, PolicyDecision
from execution.gateway.capabilities import CAP_SHELL_RUN, CAP_TEST_RUN
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
}


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
        )
        return to_output(
            result,
            context.workspace,
            failure_category="PROCESS_FAILURE",
            decision=decision,
        )


# -------------------------------------------------------------- test.run


class NoArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")


class RunTestTool(Tool):
    spec = ToolSpec(
        name="test.run",
        action_type="RUN_TEST",
        description="Run the workspace test suite (python -m pytest).",
        args_model=NoArgs,
        capabilities=frozenset({CAP_TEST_RUN}),
        read_only=False,
        timeout_seconds=150,
    )

    def run(self, context: ToolContext, args: NoArgs) -> ToolOutput:
        result = run_process(
            ["python", "-m", "pytest"],
            context.workspace,
            limits=ProcessLimits(timeout_seconds=120),
            cancel=context.cancel,
            workspace=context.workspace,
        )
        return to_output(
            result, context.workspace, failure_category="TEST_FAILURE"
        )
