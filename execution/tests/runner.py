from execution.commands.runner import run_command


def run_tests(worktree: str) -> dict:
    """
    Run the test suite inside a worktree.
    """

    result = run_command(
        ["python", "-m", "pytest"],
        cwd=worktree,
        timeout=30,
    )

    return {
        "status": result["status"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "exit_code": result["exit_code"],
    }
