"""Trusted-caller command helper over the hardened process runner.

`run_command` is for code inside the execution layer (test runner, demo).
It applies the same environment scrubbing, process-tree kill, output
bounds and cancellation as `shell.run`, but no command *policy*: whoever
calls it has already decided what to run. Model-supplied commands must go
through the `shell.run` tool, which adds the policy.
"""

import threading
from pathlib import Path

from execution.commands.process import ProcessLimits, ProcessResult, run_process


def result_to_dict(result: ProcessResult) -> dict:
    return {
        "command": result.argv,
        "cwd": result.cwd,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "status": result.status,
        "duration_ms": result.duration_ms,
        "truncated": result.truncated,
        "stdout_total": result.stdout_total,
        "stderr_total": result.stderr_total,
        "output_files": result.output_files,
        "tree": result.tree,
        "error_kind": result.error_kind,
        "run_id": result.run_id,
    }


def run_command(
    command: list[str],
    cwd: str,
    timeout: float = 30,
    *,
    cancel: threading.Event | None = None,
    env_extra: dict[str, str] | None = None,
    limits: ProcessLimits | None = None,
    workspace: str | Path | None = None,
) -> dict:
    """Run a command and return stdout, stderr, exit code and a status.

    Status is PASS, FAIL (non-zero exit), TIMEOUT, CANCELLED or ERROR
    (could not start: missing executable, bad cwd, ...).
    """

    if limits is None:
        limits = ProcessLimits(timeout_seconds=timeout)

    result = run_process(
        command,
        cwd,
        env_extra=env_extra,
        limits=limits,
        cancel=cancel,
        workspace=Path(workspace) if workspace is not None else None,
    )
    return result_to_dict(result)
