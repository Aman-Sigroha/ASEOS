import threading

from execution.commands.runner import run_command


def run_tests(
    worktree: str,
    timeout: float = 30,
    cancel: threading.Event | None = None,
) -> dict:
    """
    Run the test suite inside a worktree.
    """

    result = run_command(
        ["python", "-m", "pytest"],
        cwd=worktree,
        timeout=timeout,
        cancel=cancel,
    )

    return {
        "status": result["status"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "exit_code": result["exit_code"],
        "duration_ms": result["duration_ms"],
        "truncated": result["truncated"],
        "output_files": result["output_files"],
        "tree": result["tree"],
        "error_kind": result["error_kind"],
        "run_id": result["run_id"],
    }
