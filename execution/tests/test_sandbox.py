import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from execution.commands.process import ProcessLimits
from execution.gateway import ExecutionGateway
from execution.gateway.tools import default_tools
from execution.sandbox.base import SandboxError
from execution.sandbox.container import ContainerConfig, ContainerSandbox
from execution.security.levels import DEV, ELEVATED, WORKTREE_EDIT
from execution.tests.proc_helpers import wait_for_file, wait_until_dead
from runtime.schemas.action import Action

FAKE = Path(__file__).with_name("fake_engine.py")
PASSING_TEST = "def test_ok():\n    assert 1 + 1 == 2\n"
SLOW_TEST = (
    "import pathlib, subprocess, sys, time\n"
    "def test_slow():\n"
    "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "    pathlib.Path('child.pid').write_text(str(child.pid))\n"
    "    time.sleep(120)\n"
)
ENV_TEST = (
    "import json, os, pathlib\n"
    "def test_env():\n"
    "    pathlib.Path('seen_env.json').write_text(json.dumps(sorted(os.environ)))\n"
)

FORBIDDEN_FLAGS = {
    "--privileged", "--net=host", "--network=host", "--pid=host", "--pid",
    "--ipc=host", "--ipc", "--uts=host", "--userns=host", "-v", "--volume",
    "--device", "--cap-add", "--add-host", "--env-file", "-p", "--publish",
    "-P", "--security-opt=seccomp=unconfined", "--security-opt=apparmor=unconfined",
}


@pytest.fixture
def workspace(tmp_path):
    root = (tmp_path / "ws").resolve()
    root.mkdir()
    return root


@pytest.fixture
def engine_log(tmp_path):
    return tmp_path / "engine.log"


def fake_sandbox(engine_log, **config):
    return ContainerSandbox(
        ContainerConfig(
            engine_command=(sys.executable, str(FAKE), str(engine_log)),
            **config,
        )
    )


def entries(engine_log):
    if not engine_log.exists():
        return []

    return [json.loads(line) for line in engine_log.read_text().splitlines()]


def build(sandbox, workspace, **kw):
    kw.setdefault("cwd", workspace)
    kw.setdefault("env", {"CI": "1"})
    kw.setdefault("limits", ProcessLimits())
    kw.setdefault("network", False)
    return sandbox.run_args(
        ["python", "-m", "pytest"], workspace=workspace, name="aseos-run-x", **kw
    )


# --------------------------------------------------- the engine command line


def test_the_run_command_has_every_isolation_flag(workspace):
    args = build(ContainerSandbox(), workspace)

    def value(flag):
        return args[args.index(flag) + 1]

    assert args[:3] == ["docker", "run", "--rm"]
    assert value("--network") == "none"
    assert "--read-only" in args
    assert value("--cap-drop") == "ALL"
    assert value("--security-opt") == "no-new-privileges"
    assert value("--user") == "65532:65532"
    assert value("--pids-limit") == "256"
    assert value("--memory") == value("--memory-swap")
    assert "core=0" in args
    assert value("--tmpfs").startswith("/tmp:rw,nosuid,size=256m")
    assert "noexec" in value("--tmpfs")
    assert args[-3:] == ["python", "-m", "pytest"]


def test_nothing_that_widens_the_boundary_is_ever_requested(workspace):
    for network in (False, True):
        args = build(ContainerSandbox(), workspace, network=network)

        assert FORBIDDEN_FLAGS.isdisjoint(args)
        assert not any("docker.sock" in a for a in args)
        assert not any(a.startswith("--net=") for a in args)


def test_only_the_workspace_is_mounted(workspace, tmp_path):
    args = build(ContainerSandbox(), workspace)
    mounts = [args[i + 1] for i, a in enumerate(args) if a == "--mount"]

    assert mounts == [f"type=bind,source={workspace},target=/workspace"]
    assert str(tmp_path) not in " ".join(a for a in args if a not in mounts)


def test_the_workspace_can_be_mounted_read_only(workspace):
    sandbox = ContainerSandbox(ContainerConfig(read_only_workspace=True))

    args = build(sandbox, workspace)

    assert next(a for a in args if a.startswith("type=bind")).endswith(",readonly")


def test_network_is_only_enabled_on_request(workspace):
    off = build(ContainerSandbox(), workspace, network=False)
    on = build(ContainerSandbox(), workspace, network=True)

    assert off[off.index("--network") + 1] == "none"
    assert on[on.index("--network") + 1] == "bridge"


def test_resource_limits_follow_the_process_limits(workspace):
    limits = ProcessLimits(max_memory_bytes=512 * 1024**2, max_processes=17)

    args = build(ContainerSandbox(), workspace, limits=limits)

    assert args[args.index("--memory") + 1] == str(512 * 1024**2)
    assert args[args.index("--pids-limit") + 1] == "17"


def test_working_directory_maps_into_the_container(workspace):
    sub = workspace / "pkg" / "tests"
    sub.mkdir(parents=True)

    args = build(ContainerSandbox(), workspace, cwd=sub)

    assert args[args.index("--workdir") + 1] == "/workspace/pkg/tests"


def test_host_environment_never_crosses_into_the_container(workspace):
    env = {
        "CI": "1",
        "PYTHONUTF8": "1",
        "PATH": r"C:\Windows",
        "AWS_SECRET_ACCESS_KEY": "k",
        "GITHUB_TOKEN": "t",
        "USERPROFILE": r"C:\Users\me",
        "TEMP": r"C:\Temp",
        "SYSTEMROOT": r"C:\Windows",
    }

    args = build(ContainerSandbox(), workspace, env=env)
    passed = [args[i + 1] for i, a in enumerate(args) if a == "-e"]

    assert sorted(passed) == ["CI=1", "HOME=/tmp", "PYTHONUTF8=1"]


def test_unsafe_requests_are_refused_not_adapted(workspace, tmp_path):
    sandbox = ContainerSandbox()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    comma = tmp_path / "a,b"
    comma.mkdir()

    with pytest.raises(SandboxError, match="outside the workspace"):
        build(sandbox, workspace, cwd=outside)

    with pytest.raises(SandboxError, match="cannot be mounted"):
        build(sandbox, comma.resolve(), cwd=comma.resolve())

    with pytest.raises(SandboxError):
        build(sandbox, workspace, env={"CI": "a\0b"})


@pytest.mark.parametrize(
    "config",
    [
        {"engine": "lxc"},
        {"image": "--privileged"},
        {"image": "a b"},
        {"image": ""},
        {"user": "root"},
        {"user": "0:0; rm"},
        {"engine_command": ()},
        {"engine_command": ("",)},
    ],
)
def test_bad_configuration_is_rejected(config):
    with pytest.raises(ValueError):
        ContainerSandbox(ContainerConfig(**config))


def test_podman_uses_the_same_flags(workspace):
    args = build(ContainerSandbox(ContainerConfig(engine="podman")), workspace)

    assert args[:2] == ["podman", "run"]
    assert "--cap-drop" in args and "--read-only" in args


# ---------------------------------------------------------- availability


def test_availability_with_a_working_engine(engine_log):
    ok, detail = fake_sandbox(engine_log).availability()

    assert ok is True
    assert "99.0.0" in detail


def test_missing_engine_is_unavailable(monkeypatch):
    monkeypatch.setenv("PATH", "")

    ok, detail = ContainerSandbox().availability()

    assert ok is False
    assert "not found" in detail


def test_unreachable_daemon_is_unavailable(engine_log):
    Path(str(engine_log) + ".down").write_text("x")

    ok, detail = fake_sandbox(engine_log).availability()

    assert ok is False
    assert "not reachable" in detail


# -------------------------------------------------- end to end (fake engine)


def make_gateway(workspace, sandbox, profile=DEV, **kw):
    return ExecutionGateway(
        str(workspace),
        tools=default_tools(),
        profile=profile,
        sandbox=sandbox,
        **kw,
    )


async def run_cmd(gateway, command, action_id="s-1", **extra):
    return await gateway.execute(
        Action(id=action_id, type="RUN_COMMAND", payload={"command": command, **extra})
    )


@pytest.mark.asyncio
async def test_shell_run_goes_through_the_sandbox(workspace, engine_log):
    (workspace / "test_ok.py").write_text(PASSING_TEST)
    gateway = make_gateway(workspace, fake_sandbox(engine_log))

    result = await run_cmd(gateway, ["python", "-m", "pytest", "-q"])

    assert result.success is True, result.stderr
    assert result.data["sandboxed"] is True
    assert result.data["process_tree"]["network"] == "none"
    run = next(e for e in entries(engine_log) if e["cmd"] == "run")
    assert run["program"] == ["python", "-m", "pytest", "-q"]
    assert "--read-only" in run["args"] and "--cap-drop" in run["args"]


@pytest.mark.asyncio
async def test_test_run_goes_through_the_sandbox_too(workspace, engine_log):
    (workspace / "test_ok.py").write_text(PASSING_TEST)
    gateway = make_gateway(workspace, fake_sandbox(engine_log))

    result = await gateway.execute(Action(id="t", type="RUN_TEST", payload={}))

    assert result.success is True, result.stderr
    assert any(e["cmd"] == "run" for e in entries(engine_log))


@pytest.mark.asyncio
async def test_host_secrets_never_reach_the_sandboxed_program(
    workspace, engine_log, monkeypatch
):
    monkeypatch.setenv("MY_API_TOKEN", "host-secret-value-123")
    monkeypatch.setenv("SOME_PLAIN_VAR", "visible-on-host")
    (workspace / "test_env.py").write_text(ENV_TEST)
    gateway = make_gateway(workspace, fake_sandbox(engine_log))

    result = await run_cmd(gateway, ["python", "-m", "pytest", "-q"], env={"CI": "1"})
    seen = {n.upper() for n in json.loads((workspace / "seen_env.json").read_text())}

    assert result.success is True, result.stderr
    assert "MY_API_TOKEN" not in seen and "SOME_PLAIN_VAR" not in seen
    run = next(e for e in entries(engine_log) if e["cmd"] == "run")
    assert "host-secret-value-123" not in json.dumps(run)


@pytest.mark.asyncio
async def test_installs_get_network_only_with_the_network_grant(workspace, engine_log):
    sandbox = fake_sandbox(engine_log)
    gateway = make_gateway(workspace, sandbox, profile=ELEVATED)

    # `pip --version` style probe through the install rule is not possible
    # without a real pip; check the decision path with an install command.
    result = await run_cmd(
        gateway, ["python", "-m", "pip", "install", "--help"], timeout_seconds=30
    )

    run = [e for e in entries(engine_log) if e["cmd"] == "run"]
    assert run, result.stderr
    args = run[0]["args"]
    assert args[args.index("--network") + 1] == "bridge"


@pytest.mark.asyncio
async def test_timeout_terminates_the_container_and_cleans_up(workspace, engine_log):
    (workspace / "test_slow.py").write_text(SLOW_TEST)
    gateway = make_gateway(workspace, fake_sandbox(engine_log))

    result = await run_cmd(
        gateway, ["python", "-m", "pytest", "-q"], timeout_seconds=15
    )

    assert result.error_category == "TIMEOUT"
    kinds = [e["cmd"] for e in entries(engine_log)]
    assert "kill" in kinds and "rm" in kinds
    names = {e["args"][-1] for e in entries(engine_log) if e["cmd"] in ("kill", "rm")}
    assert len(names) == 1 and next(iter(names)).startswith("aseos-run-")


@pytest.mark.asyncio
async def test_cancel_terminates_the_container(workspace, engine_log):
    (workspace / "test_slow.py").write_text(SLOW_TEST)
    gateway = make_gateway(workspace, fake_sandbox(engine_log))
    task = asyncio.create_task(
        run_cmd(gateway, ["python", "-m", "pytest", "-q"], action_id="c-1")
    )

    assert await asyncio.to_thread(wait_for_file, workspace / "child.pid", 60)
    child = int((workspace / "child.pid").read_text())
    gateway.cancel("c-1")
    result = await asyncio.wait_for(task, 60)

    assert result.error_category == "CANCELLED"
    assert wait_until_dead(child)
    assert "kill" in [e["cmd"] for e in entries(engine_log)]


@pytest.mark.asyncio
async def test_a_normal_exit_does_not_spawn_cleanup_commands(workspace, engine_log):
    (workspace / "test_ok.py").write_text(PASSING_TEST)
    gateway = make_gateway(workspace, fake_sandbox(engine_log))

    await run_cmd(gateway, ["python", "-m", "pytest", "-q"])

    assert [e["cmd"] for e in entries(engine_log)] == ["run"]


# ---------------------------------------------------------- fail closed


@pytest.mark.asyncio
async def test_required_sandbox_that_is_missing_refuses_to_run(workspace):
    (workspace / "test_ok.py").write_text(PASSING_TEST)
    gateway = make_gateway(workspace, None)  # dev profile requires a sandbox

    shell = await run_cmd(gateway, ["python", "-m", "pytest", "-q"])
    tests = await gateway.execute(Action(id="t", type="RUN_TEST", payload={}))

    for result in (shell, tests):
        assert result.error_category == "PERMISSION_DENIED"
        assert "refusing to run on the host" in result.stderr

    assert not (workspace / ".pytest_cache").exists()


@pytest.mark.asyncio
async def test_unavailable_sandbox_never_falls_back_to_the_host(
    workspace, engine_log
):
    Path(str(engine_log) + ".down").write_text("x")
    marker = workspace / "ran_on_host.txt"
    (workspace / "test_marker.py").write_text(
        "import pathlib\ndef test_marker():\n    pathlib.Path('ran_on_host.txt').write_text('x')\n"
    )
    gateway = make_gateway(workspace, fake_sandbox(engine_log))

    result = await run_cmd(gateway, ["python", "-m", "pytest", "-q"])

    assert result.error_category == "PERMISSION_DENIED"
    assert "unavailable" in result.stderr
    assert not marker.exists()


@pytest.mark.asyncio
async def test_a_configured_sandbox_is_used_even_when_not_required(
    workspace, engine_log
):
    Path(str(engine_log) + ".down").write_text("x")
    gateway = make_gateway(
        workspace, fake_sandbox(engine_log), profile=None, require_sandbox=False,
        granted_capabilities={"fs.read", "shell.run"},
    )

    result = await run_cmd(gateway, ["python", "--version"])

    assert result.error_category == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_without_profile_or_sandbox_behaviour_is_unchanged(workspace):
    gateway = ExecutionGateway(
        str(workspace),
        tools=default_tools(),
        granted_capabilities={"fs.read", "shell.run"},
    )

    result = await run_cmd(gateway, ["python", "--version"])

    assert result.success is True, result.stderr
    assert result.data["sandboxed"] is False


@pytest.mark.asyncio
async def test_a_sandbox_that_raises_is_treated_as_a_refusal(workspace, engine_log):
    class Broken(ContainerSandbox):
        def prepare(self, *a, **k):
            raise RuntimeError("backend bug")

    sandbox = Broken(
        ContainerConfig(engine_command=(sys.executable, str(FAKE), str(engine_log)))
    )
    gateway = make_gateway(workspace, sandbox)

    result = await run_cmd(gateway, ["python", "--version"])

    assert result.error_category == "PERMISSION_DENIED"
    assert "backend bug" in result.stderr


# ------------------------------------------------------ real engine (opt-in)


def _real_engine_ready():
    sandbox = ContainerSandbox()

    if not sandbox.availability()[0]:
        return False

    probe = subprocess.run(
        ["docker", "image", "inspect", sandbox.config.image], capture_output=True
    )
    return probe.returncode == 0


@pytest.mark.skipif(
    not _real_engine_ready(),
    reason="needs a running Docker daemon and the python:3.12-slim image",
)
@pytest.mark.asyncio
async def test_real_container_blocks_network_and_host_files(workspace):
    (workspace / "test_isolation.py").write_text(
        "import socket, os\n"
        "def test_no_network():\n"
        "    try:\n"
        "        socket.create_connection(('1.1.1.1', 53), timeout=3)\n"
        "    except OSError:\n"
        "        return\n"
        "    raise AssertionError('network reachable')\n"
        "def test_no_host_paths():\n"
        "    assert not os.path.exists('/Users') and not os.path.exists('C:\\\\')\n"
        "def test_root_fs_is_read_only():\n"
        "    try:\n"
        "        open('/etc/aseos_probe', 'w')\n"
        "    except OSError:\n"
        "        return\n"
        "    raise AssertionError('root filesystem is writable')\n"
    )
    gateway = make_gateway(workspace, ContainerSandbox())

    result = await run_cmd(gateway, ["python", "-m", "pytest", "-q"], timeout_seconds=120)

    assert result.success is True, result.stdout + result.stderr
