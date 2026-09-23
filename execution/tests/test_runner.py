import sys

from execution.commands.runner import run_command


def test_run_command_success(tmp_path):
    result = run_command(
        [sys.executable, "-c", "print('hello aseos')"],
        str(tmp_path),
    )

    assert result["status"] == "PASS"
    assert result["exit_code"] == 0
    assert "hello aseos" in result["stdout"]


def test_run_command_failure(tmp_path):
    result = run_command(
        [sys.executable, "-c", "print('error'); raise SystemExit(1)"],
        str(tmp_path),
    )

    assert result["status"] == "FAIL"
    assert result["exit_code"] == 1
    assert "error" in result["stdout"]


def test_run_command_timeout(tmp_path):
    result = run_command(
        [sys.executable, "-c", "import time; time.sleep(2)"],
        str(tmp_path),
        timeout=1,
    )

    assert result["status"] == "TIMEOUT"
