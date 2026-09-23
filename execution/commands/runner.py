import subprocess


def run_command(
    command: list[str],
    cwd: str,
    timeout: int = 30,
) -> dict:
    """
    Execute a command inside a working directory.

    Returns stdout, stderr, exit code, and execution status.
    """

    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        return {
            "command": command,
            "cwd": cwd,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "status": "PASS" if result.returncode == 0 else "FAIL",
        }

    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "cwd": cwd,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "exit_code": None,
            "status": "TIMEOUT",
        }

    except Exception as exc:
        return {
            "command": command,
            "cwd": cwd,
            "stdout": "",
            "stderr": str(exc),
            "exit_code": None,
            "status": "ERROR",
        }
