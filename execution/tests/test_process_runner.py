import json
import os
import threading

import pytest

from execution.commands.capture import BoundedCapture
from execution.commands.env import (
    EnvironmentError_,
    build_environment,
    redact,
    validate_caller_env,
)
from execution.commands.process import ProcessLimits, run_process
from execution.tests.proc_helpers import (
    PY,
    SPAWN_CHILD_AND_SLEEP,
    SPAWN_ORPHAN_AND_EXIT,
    cancel_when_file_appears,
    pid_alive,
    wait_until_dead,
)

LIMITS = ProcessLimits(timeout_seconds=30)


@pytest.fixture
def cwd(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    return work


@pytest.fixture
def logs(tmp_path):
    return tmp_path / "logs"


def run(argv, cwd, logs, **kw):
    kw.setdefault("limits", LIMITS)
    return run_process(argv, cwd, log_dir=logs, **kw)


# ------------------------------------------------------------ basics


def test_captures_stdout_stderr_and_exit_code(cwd, logs):
    result = run(
        [PY, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"],
        cwd,
        logs,
    )

    assert result.status == "FAIL"
    assert result.exit_code == 3
    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"
    assert result.duration_ms >= 0
    assert result.output_files == {}


def test_success(cwd, logs):
    result = run([PY, "-c", "print('ok')"], cwd, logs)

    assert (result.status, result.exit_code) == ("PASS", 0)


def test_runs_in_the_requested_directory(cwd, logs):
    (cwd / "sub").mkdir()

    result = run([PY, "-c", "import os; print(os.getcwd())"], cwd / "sub", logs)

    assert os.path.samefile(result.stdout.strip(), cwd / "sub")


def test_stdin_is_closed(cwd, logs):
    result = run([PY, "-c", "import sys; print(repr(sys.stdin.read()))"], cwd, logs)

    assert result.status == "PASS"
    assert result.stdout.strip() == "''"


def test_arguments_are_never_interpreted_by_a_shell(cwd, logs):
    payload = "x && echo pwned > pwned.txt; $(whoami) | `id`"

    result = run([PY, "-c", "import sys; print(sys.argv[1])", payload], cwd, logs)

    assert result.stdout.strip() == payload
    assert not (cwd / "pwned.txt").exists()


def test_non_utf8_output_does_not_crash(cwd, logs):
    result = run(
        [PY, "-c", "import sys; sys.stdout.buffer.write(b'a\\xff\\xfeb')"], cwd, logs
    )

    assert result.status == "PASS"
    assert result.stdout.startswith("a") and result.stdout.endswith("b")


# --------------------------------------------------- startup failures


def test_missing_executable(cwd, logs):
    result = run(["definitely-not-a-real-program-xyz"], cwd, logs)

    assert (result.status, result.error_kind) == ("ERROR", "not_found")
    assert "not found" in result.error


@pytest.mark.parametrize("argv", [[], [""], ["  "], "python", [1, 2], ["a\0b"]])
def test_invalid_argv(cwd, logs, argv):
    result = run(argv, cwd, logs)

    assert (result.status, result.error_kind) == ("ERROR", "invalid_argv")


def test_invalid_cwd(cwd, logs):
    missing = run([PY, "-c", "pass"], cwd / "nope", logs)
    a_file = cwd / "f.txt"
    a_file.write_text("x")
    not_dir = run([PY, "-c", "pass"], a_file, logs)

    assert missing.error_kind == "invalid_cwd"
    assert not_dir.error_kind == "invalid_cwd"


def test_cwd_must_be_inside_the_workspace(cwd, logs, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()

    escaped = run([PY, "-c", "pass"], outside, logs, workspace=cwd)
    inside = run([PY, "-c", "pass"], cwd, logs, workspace=cwd)

    assert escaped.error_kind == "invalid_cwd"
    assert "outside the workspace" in escaped.error
    assert inside.status == "PASS"


@pytest.mark.skipif(os.name != "nt", reason="batch files are Windows-only")
def test_batch_files_reject_metacharacters_in_arguments(cwd, logs):
    script = cwd / "tool.cmd"
    script.write_text("@echo off\r\necho got:%1\r\n")

    safe = run([str(script), "hello"], cwd, logs)
    unsafe = run([str(script), "a&calc.exe"], cwd, logs)

    assert safe.status == "PASS" and "got:hello" in safe.stdout
    assert unsafe.error_kind == "unsafe_argument"


# ------------------------------------------------- timeout and kill


def test_timeout_returns_partial_output_and_kills_the_process(cwd, logs):
    code = "import time; print('started', flush=True); time.sleep(120)"

    result = run(
        [PY, "-c", code], cwd, logs, limits=ProcessLimits(timeout_seconds=3)
    )

    assert result.status == "TIMEOUT"
    assert "started" in result.stdout
    assert result.tree["killed"] is True
    assert result.tree["leftover"] == 0


def test_timeout_kills_the_whole_process_tree(cwd, logs):
    marker = cwd / "child.pid"

    def stop_when_child_exists():
        cancel_when_file_appears(marker, cancel)

    cancel = threading.Event()
    stop_when_child_exists()

    result = run([PY, "-c", SPAWN_CHILD_AND_SLEEP], cwd, logs, cancel=cancel)

    child = int(marker.read_text())
    assert result.status == "CANCELLED"
    assert result.tree["leftover"] == 0
    assert wait_until_dead(child), "grandchild survived the kill"


def test_real_timeout_also_kills_children(cwd, logs):
    marker = cwd / "child.pid"

    result = run(
        [PY, "-c", SPAWN_CHILD_AND_SLEEP],
        cwd,
        logs,
        limits=ProcessLimits(timeout_seconds=6),
    )

    assert result.status == "TIMEOUT"

    if marker.exists():  # the child may not have started on a very slow host
        assert wait_until_dead(int(marker.read_text()))


def test_orphans_left_behind_by_a_finished_process_are_killed(cwd, logs):
    result = run([PY, "-c", SPAWN_ORPHAN_AND_EXIT], cwd, logs)

    child = int((cwd / "child.pid").read_text())
    assert result.status == "PASS"
    assert result.tree.get("orphans") is True
    assert wait_until_dead(child), "orphaned grandchild survived"


def test_cancel_before_start_is_honoured_quickly(cwd, logs):
    cancel = threading.Event()
    cancel.set()

    result = run([PY, "-c", "import time; time.sleep(60)"], cwd, logs, cancel=cancel)

    assert result.status == "CANCELLED"
    assert result.duration_ms < 20_000


def test_a_finished_process_is_not_reported_as_cancelled(cwd, logs):
    cancel = threading.Event()

    result = run([PY, "-c", "print('done')"], cwd, logs, cancel=cancel)

    assert result.status == "PASS"


# ------------------------------------------------------ output bounds


def test_large_output_is_bounded_and_fully_spooled(cwd, logs):
    limits = ProcessLimits(timeout_seconds=60, head_bytes=100, tail_bytes=100)

    result = run(
        [PY, "-c", "import sys; sys.stdout.write('0123456789' * 300_000)"],
        cwd,
        logs,
        limits=limits,
    )

    assert result.status == "PASS"
    assert result.stdout_total == 3_000_000
    assert result.stdout_truncated is True
    assert len(result.stdout) < 500
    assert "bytes omitted" in result.stdout
    assert result.stdout.startswith("0123456789")
    spool = result.output_files["stdout"]
    assert os.path.getsize(spool) == 3_000_000
    assert open(spool, "rb").read(20) == b"01234567890123456789"


def test_small_output_is_not_spooled_or_truncated(cwd, logs):
    result = run([PY, "-c", "print('hi')"], cwd, logs)

    assert result.stdout_truncated is False
    assert result.output_files == {}
    assert not logs.exists() or list(logs.iterdir()) == []


def test_spool_has_its_own_cap(cwd, logs):
    limits = ProcessLimits(
        timeout_seconds=60, head_bytes=10, tail_bytes=10, max_spool_bytes=1000
    )

    result = run(
        [PY, "-c", "import sys; sys.stdout.write('x' * 100_000)"],
        cwd,
        logs,
        limits=limits,
    )

    assert result.spool_truncated is True
    assert os.path.getsize(result.output_files["stdout"]) == 1000
    assert result.stdout_total == 100_000


def test_a_process_flooding_both_streams_cannot_stall_the_runner(cwd, logs):
    code = (
        "import sys\n"
        "for i in range(2000):\n"
        "    sys.stdout.write('o' * 1000); sys.stderr.write('e' * 1000)\n"
    )

    result = run([PY, "-c", code], cwd, logs, limits=ProcessLimits(timeout_seconds=60))

    assert result.status == "PASS"
    assert result.stdout_total == result.stderr_total == 2_000_000


def test_a_chatty_process_that_hangs_still_times_out(cwd, logs):
    code = (
        "import sys, time\n"
        "sys.stdout.write('x' * 5_000_000); sys.stdout.flush()\n"
        "time.sleep(120)\n"
    )

    result = run(
        [PY, "-c", code], cwd, logs, limits=ProcessLimits(timeout_seconds=6)
    )

    assert result.status == "TIMEOUT"
    assert result.stdout_total >= 1


# ---------------------------------------------------------- capture


def test_capture_keeps_head_and_tail_and_exact_total(tmp_path):
    capture = BoundedCapture(5, 5, tmp_path / "log", 10_000)

    for chunk in (b"AAAAA", b"mid1", b"mid2", b"ZZZZZ"):
        capture.feed(chunk)

    capture.close()

    assert capture.total == 18
    assert capture.truncated is True
    assert bytes(capture.head) == b"AAAAA"
    assert bytes(capture.tail) == b"ZZZZZ"
    assert (tmp_path / "log").read_bytes() == b"AAAAAmid1mid2ZZZZZ"


def test_capture_below_the_limit_returns_everything(tmp_path):
    capture = BoundedCapture(50, 50, tmp_path / "log", 1000)
    capture.feed(b"hello ")
    capture.feed(b"world")
    capture.close()

    assert capture.text() == "hello world"
    assert not (tmp_path / "log").exists()


# -------------------------------------------------------- environment


def test_secrets_and_code_loading_variables_are_not_inherited(
    cwd, logs, monkeypatch
):
    for name in (
        "AWS_SECRET_ACCESS_KEY",
        "GITHUB_TOKEN",
        "MY_PASSWORD",
        "OPENAI_API_KEY",
        "DATABASE_URL_SECRET",
        "PYTHONPATH",
        "NODE_OPTIONS",
        "LD_PRELOAD",
        "ANTHROPIC_API_KEY",
        "SOME_RANDOM_VAR",
    ):
        monkeypatch.setenv(name, "leaky-value-12345")

    result = run(
        [PY, "-c", "import os, json; print(json.dumps(sorted(os.environ)))"],
        cwd,
        logs,
    )
    names = {n.upper() for n in json.loads(result.stdout)}

    assert result.status == "PASS"
    assert names.isdisjoint(
        {
            "AWS_SECRET_ACCESS_KEY",
            "GITHUB_TOKEN",
            "MY_PASSWORD",
            "OPENAI_API_KEY",
            "DATABASE_URL_SECRET",
            "PYTHONPATH",
            "NODE_OPTIONS",
            "LD_PRELOAD",
            "ANTHROPIC_API_KEY",
            "SOME_RANDOM_VAR",
        }
    )
    assert "PATH" in names
    assert {"TEMP", "TMP", "TMPDIR"} <= names
    assert "leaky-value-12345" not in result.stdout


def test_each_run_gets_its_own_temp_dir_that_is_removed(cwd, logs):
    code = "import os, tempfile; print(os.environ['TEMP'], tempfile.gettempdir())"

    first = run([PY, "-c", code], cwd, logs)
    second = run([PY, "-c", code], cwd, logs)
    temp1, check1 = first.stdout.split()

    assert temp1 == check1
    assert temp1 != second.stdout.split()[0]
    assert not os.path.exists(temp1)


def test_caller_environment_is_allow_listed(cwd, logs):
    ok = run(
        [PY, "-c", "import os; print(os.environ['CI'])"],
        cwd,
        logs,
        env_extra={"CI": "true"},
    )

    assert ok.stdout.strip() == "true"

    for name in ("PYTHONPATH", "PATH", "LD_PRELOAD", "NODE_OPTIONS", "SECRET"):
        bad = run([PY, "-c", "pass"], cwd, logs, env_extra={name: "x"})
        assert bad.error_kind == "invalid_env", name


def test_environment_validation_details():
    with pytest.raises(EnvironmentError_):
        validate_caller_env({"1BAD": "x"})

    with pytest.raises(EnvironmentError_):
        validate_caller_env({"CI": "x" * 5000})

    with pytest.raises(EnvironmentError_):
        validate_caller_env({"CI": "a\0b"})

    assert validate_caller_env({"ci": "1"}) == {"ci": "1"}


def test_build_environment_is_explicit(tmp_path):
    env = build_environment(
        tmp_path,
        {"CI": "1"},
        source={
            "PATH": "/bin",
            "HOME": "/home/u",
            "MY_TOKEN": "t",
            "RANDOM": "r",
            "SECRETS_HOME": "s",
        },
    )

    assert env["PATH"] == "/bin" and env["HOME"] == "/home/u"
    assert env["CI"] == "1"
    assert "MY_TOKEN" not in env and "RANDOM" not in env
    assert env["TEMP"] == str(tmp_path)
    assert env["PYTHONUTF8"] == "1"


def test_secret_values_are_redacted_from_output(cwd, logs, monkeypatch):
    monkeypatch.setenv("DEPLOY_TOKEN", "tok_1234567890abcdef")

    result = run([PY, "-c", "print('leaking tok_1234567890abcdef now')"], cwd, logs)

    assert "tok_1234567890abcdef" not in result.stdout
    assert "[REDACTED]" in result.stdout


def test_redact_ignores_short_values():
    assert redact("abc", {"MY_TOKEN": "abc"}) == "abc"


# ------------------------------------------------------ resource limits


@pytest.mark.skipif(os.name != "nt", reason="job-object memory limit")
def test_memory_limit_stops_a_runaway_allocation(cwd, logs):
    limits = ProcessLimits(timeout_seconds=60, max_memory_bytes=300 * 1024 * 1024)

    result = run(
        [PY, "-c", "x = bytearray(2_000_000_000); print('allocated')"],
        cwd,
        logs,
        limits=limits,
    )

    assert result.status == "FAIL"
    assert "allocated" not in result.stdout
    assert "MemoryError" in result.stderr


@pytest.mark.skipif(os.name != "nt", reason="job-object process limit")
def test_process_limit_caps_the_tree(cwd, logs):
    code = (
        "import subprocess, sys, time\n"
        "kids = []\n"
        "for i in range(12):\n"
        "    try:\n"
        "        kids.append(subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']))\n"
        "    except OSError:\n"
        "        pass\n"
        "time.sleep(3)\n"
        "print(sum(1 for k in kids if k.poll() is None))\n"
    )
    limits = ProcessLimits(timeout_seconds=60, max_processes=4)

    result = run([PY, "-c", code], cwd, logs, limits=limits)

    assert result.status == "PASS", result.stderr
    assert int(result.stdout.strip()) <= 3
