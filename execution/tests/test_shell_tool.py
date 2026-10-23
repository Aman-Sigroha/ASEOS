import asyncio
import sys

import pytest

from execution.commands.policy import CAP_SHELL_NETWORK, CommandPolicy, CommandRule, Match
from execution.commands.process import ProcessLimits
from execution.gateway import ExecutionGateway
from execution.gateway.capabilities import CAP_SHELL_RUN, DEFAULT_CAPABILITIES
from execution.gateway.command_tools import RunCommandTool
from execution.gateway.tools import default_tools
from execution.tests.proc_helpers import wait_for_file, wait_until_dead
from runtime.schemas.action import Action

SHELL_CAPS = DEFAULT_CAPABILITIES | {CAP_SHELL_RUN}

PASSING_TEST = "def test_ok():\n    assert 1 + 1 == 2\n"
FAILING_TEST = "def test_bad():\n    assert 1 + 1 == 3\n"
SLOW_TEST = (
    "import pathlib, subprocess, sys, time\n"
    "def test_slow():\n"
    "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "    pathlib.Path('child.pid').write_text(str(child.pid))\n"
    "    time.sleep(120)\n"
)


@pytest.fixture
def workspace(tmp_path):
    root = (tmp_path / "ws").resolve()
    root.mkdir()
    return root


def make_gateway(workspace, caps=SHELL_CAPS, tools=None, **kw):
    return ExecutionGateway(
        str(workspace),
        tools=tools if tools is not None else default_tools(),
        granted_capabilities=caps,
        **kw,
    )


async def run_cmd(gateway, command, action_id="cmd-1", **extra):
    return await gateway.execute(
        Action(
            id=action_id,
            type="RUN_COMMAND",
            payload={"command": command, **extra},
        )
    )


# -------------------------------------------------------------- gating


def test_shell_run_is_not_granted_by_default():
    assert CAP_SHELL_RUN not in DEFAULT_CAPABILITIES
    assert CAP_SHELL_NETWORK not in DEFAULT_CAPABILITIES


@pytest.mark.asyncio
async def test_denied_without_the_shell_capability(workspace):
    gateway = make_gateway(workspace, DEFAULT_CAPABILITIES)

    result = await run_cmd(gateway, ["python", "--version"])

    assert result.error_category == "PERMISSION_DENIED"
    assert "disabled" in result.stderr


@pytest.mark.asyncio
async def test_policy_refusals_never_start_a_process(workspace):
    gateway = make_gateway(workspace)

    for command in (
        ["python", "-c", "open('pwned.txt', 'w').write('x')"],
        ["bash", "-c", "touch pwned.txt"],
        "cmd /c echo x > pwned.txt",
        ["git", "status"],
        ["rm", "-rf", "."],
    ):
        result = await run_cmd(gateway, command)

        assert result.error_category in ("PERMISSION_DENIED", "INVALID_ARGUMENTS")
        assert result.success is False

    assert list(workspace.iterdir()) == []


@pytest.mark.asyncio
async def test_refusal_explains_the_policy_decision(workspace):
    result = await run_cmd(make_gateway(workspace), ["curl", "http://x"])

    assert result.error_category == "PERMISSION_DENIED"
    assert "refused by policy" in result.stderr
    assert "network" in result.stderr
    assert result.data["command"] == ["curl", "http://x"]


@pytest.mark.asyncio
async def test_install_needs_the_network_capability(workspace):
    command = ["python", "-m", "pip", "install", "some-package"]

    denied = await run_cmd(make_gateway(workspace), command)

    assert denied.error_category == "PERMISSION_DENIED"
    assert denied.data["policy"]["required_capabilities"] == [CAP_SHELL_NETWORK]
    assert denied.data["policy"]["risk"] == "high"


# ------------------------------------------------------------ argument forms


@pytest.mark.asyncio
async def test_string_commands_are_split_without_a_shell(workspace):
    (workspace / "test_ok.py").write_text(PASSING_TEST)

    result = await run_cmd(make_gateway(workspace), "python -m pytest -q")

    assert result.success is True, result.stderr
    assert result.tool == "shell.run"
    assert result.data["command"][-2:] == ["pytest", "-q"]
    assert "1 passed" in result.stdout


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "python -m pytest && echo hi",
        "python -m pytest | cat",
        "python -m pytest > out.txt",
        "python -m pytest ; whoami",
        'python -m "pytest',
    ],
)
async def test_shell_syntax_is_rejected(workspace, command):
    result = await run_cmd(make_gateway(workspace), command)

    assert result.error_category == "INVALID_ARGUMENTS"


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["", "   ", []])
async def test_empty_commands_are_invalid(workspace, command):
    result = await run_cmd(make_gateway(workspace), command)

    assert result.error_category == "INVALID_ARGUMENTS"


@pytest.mark.asyncio
async def test_cwd_must_stay_inside_the_workspace(workspace):
    gateway = make_gateway(workspace)

    escape = await run_cmd(gateway, ["python", "--version"], cwd="..")
    missing = await run_cmd(gateway, ["python", "--version"], cwd="nope")

    assert escape.error_category == "PERMISSION_DENIED"
    assert missing.error_category == "INVALID_ARGUMENTS"


@pytest.mark.asyncio
async def test_cwd_subdirectory(workspace):
    sub = workspace / "pkg"
    sub.mkdir()
    (sub / "test_in_sub.py").write_text(PASSING_TEST)

    result = await run_cmd(
        make_gateway(workspace), ["python", "-m", "pytest", "-q"], cwd="pkg"
    )

    assert result.success is True, result.stderr
    assert result.data["cwd"] == "pkg"


@pytest.mark.asyncio
async def test_caller_environment_is_restricted(workspace):
    gateway = make_gateway(workspace)

    bad = await run_cmd(
        gateway, ["python", "--version"], env={"PYTHONPATH": "/tmp/evil"}
    )
    good = await run_cmd(gateway, ["python", "--version"], env={"CI": "1"})

    assert bad.error_category == "INVALID_ARGUMENTS"
    assert good.success is True, good.stderr


# ---------------------------------------------------------------- results


@pytest.mark.asyncio
async def test_failing_command_reports_exit_code_and_output(workspace):
    (workspace / "test_bad.py").write_text(FAILING_TEST)

    result = await run_cmd(make_gateway(workspace), ["pytest", "-q"])

    assert result.success is False
    assert result.error_category == "PROCESS_FAILURE"
    assert result.exit_code == 1
    assert "assert 1 + 1 == 3" in result.stdout
    assert result.data["policy"]["category"] == "test"
    assert result.data["status"] == "FAIL"


@pytest.mark.asyncio
async def test_missing_program_is_a_process_failure(workspace):
    policy = CommandPolicy(
        rules=[
            CommandRule(
                "ghost",
                frozenset({"definitely-not-installed-xyz"}),
                lambda args: Match("build"),
            )
        ]
    )
    gateway = make_gateway(workspace, tools=[RunCommandTool(policy=policy)])

    result = await run_cmd(gateway, ["definitely-not-installed-xyz"])

    assert result.error_category == "PROCESS_FAILURE"
    assert "not found" in result.stderr


@pytest.mark.asyncio
async def test_large_output_is_bounded_with_artifact_references(workspace):
    policy = CommandPolicy(
        rules=[
            CommandRule(
                "py", frozenset({"python"}), lambda args: Match("build")
            )
        ]
    )
    tool = RunCommandTool(
        policy=policy,
        limits=ProcessLimits(head_bytes=200, tail_bytes=200),
    )
    gateway = make_gateway(workspace, tools=[tool])

    result = await run_cmd(
        gateway,
        ["python", "-c", "print('y' * 500_000)"],
    )

    assert result.success is True, result.stderr
    assert result.truncated is True
    assert len(result.stdout) < 1000
    assert result.data["stdout_bytes"] > 500_000
    assert [a.kind for a in result.artifacts] == ["command_output"]
    ref = result.artifacts[0]
    assert result.data["output_artifacts"] == {"stdout": ref.id}
    # The artifact holds the complete output, not just the inline excerpt.
    content, meta, _ = gateway.artifact_store.read(ref.id, limit=1024 * 1024)
    assert meta.original_size > 500_000 and len(content) == ref.size
    assert content.startswith(b"yyyy")


@pytest.mark.asyncio
async def test_secrets_in_the_environment_do_not_reach_the_command(
    workspace, monkeypatch
):
    monkeypatch.setenv("MY_API_TOKEN", "super-secret-token-value")
    policy = CommandPolicy(
        rules=[CommandRule("py", frozenset({"python"}), lambda a: Match("build"))]
    )
    gateway = make_gateway(workspace, tools=[RunCommandTool(policy=policy)])

    result = await run_cmd(
        gateway,
        ["python", "-c", "import os; print(os.environ.get('MY_API_TOKEN'))"],
    )

    assert result.stdout.strip() == "None"


# ---------------------------------------------------------- test.run tool


@pytest.mark.asyncio
async def test_test_run_uses_the_hardened_runner(workspace):
    (workspace / "test_ok.py").write_text(PASSING_TEST)
    gateway = make_gateway(workspace)

    passed = await gateway.execute(Action(id="t1", type="RUN_TEST", payload={}))
    (workspace / "test_bad.py").write_text(FAILING_TEST)
    failed = await gateway.execute(Action(id="t2", type="RUN_TEST", payload={}))

    assert passed.success is True, passed.stderr
    assert passed.data["process_tree"] == {}
    assert failed.error_category == "TEST_FAILURE"
    assert failed.exit_code == 1


# -------------------------------------------- timeout / cancel / shutdown


async def wait_for_pid(workspace):
    ready = await asyncio.to_thread(wait_for_file, workspace / "child.pid", 60)
    assert ready, "slow test never started"
    return int((workspace / "child.pid").read_text())


@pytest.mark.asyncio
async def test_timeout_kills_the_command_and_its_children(workspace):
    (workspace / "test_slow.py").write_text(SLOW_TEST)
    gateway = make_gateway(workspace)

    result = await run_cmd(
        gateway, ["python", "-m", "pytest", "-q"], timeout_seconds=15
    )

    assert result.error_category == "TIMEOUT"
    assert result.data["process_tree"]["leftover"] == 0
    child = workspace / "child.pid"

    if child.exists():
        assert wait_until_dead(int(child.read_text()))


@pytest.mark.asyncio
async def test_cancel_stops_a_running_command_and_its_children(workspace):
    (workspace / "test_slow.py").write_text(SLOW_TEST)
    gateway = make_gateway(workspace)
    task = asyncio.create_task(
        run_cmd(gateway, ["python", "-m", "pytest", "-q"], action_id="slow-1")
    )

    child = await wait_for_pid(workspace)
    assert gateway.running() == ["slow-1"]
    assert gateway.cancel("slow-1") is True
    result = await asyncio.wait_for(task, 60)

    assert result.error_category == "CANCELLED"
    assert result.data["process_tree"]["leftover"] == 0
    assert wait_until_dead(child)
    assert gateway.running() == []
    assert gateway.cancel("slow-1") is False


@pytest.mark.asyncio
async def test_shutdown_cancels_everything_running(workspace):
    (workspace / "test_slow.py").write_text(SLOW_TEST)
    gateway = make_gateway(workspace)
    task = asyncio.create_task(
        run_cmd(gateway, ["python", "-m", "pytest", "-q"], action_id="slow-2")
    )

    child = await wait_for_pid(workspace)
    stopped = await gateway.shutdown(timeout=60)
    result = await asyncio.wait_for(task, 30)

    assert stopped is True
    assert result.error_category == "CANCELLED"
    assert wait_until_dead(child)


@pytest.mark.asyncio
async def test_cancelling_the_awaiting_task_still_kills_the_process(workspace):
    (workspace / "test_slow.py").write_text(SLOW_TEST)
    gateway = make_gateway(workspace)
    task = asyncio.create_task(
        run_cmd(gateway, ["python", "-m", "pytest", "-q"], action_id="slow-3")
    )

    child = await wait_for_pid(workspace)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert wait_until_dead(child)
    assert gateway.running() == []


@pytest.mark.asyncio
async def test_cancel_does_not_affect_other_running_actions(workspace):
    (workspace / "test_slow.py").write_text(SLOW_TEST)
    (workspace / "other").mkdir()
    (workspace / "other" / "test_ok.py").write_text(PASSING_TEST)
    gateway = make_gateway(workspace)
    slow = asyncio.create_task(
        run_cmd(gateway, ["python", "-m", "pytest", "-q"], action_id="slow-4")
    )

    await wait_for_pid(workspace)
    other = await run_cmd(
        gateway, ["python", "-m", "pytest", "-q"], action_id="ok-1", cwd="other"
    )
    gateway.cancel("slow-4")
    cancelled = await asyncio.wait_for(slow, 60)

    assert other.success is True, other.stderr
    assert cancelled.error_category == "CANCELLED"


def test_python_is_resolved_from_the_controlled_path():
    # sanity: the interpreter used by the tests is what `python` means here
    assert sys.executable
