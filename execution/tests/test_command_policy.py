from pathlib import Path

import pytest

from execution.commands.policy import (
    CAP_SHELL_NETWORK,
    CommandPolicy,
    executable_name,
)


@pytest.fixture
def policy():
    return CommandPolicy.default()


@pytest.fixture
def workspace(tmp_path):
    root = (tmp_path / "ws").resolve()
    (root / "tests").mkdir(parents=True)
    return root


def decide(policy, workspace, argv, caps=frozenset(), cwd=None):
    return policy.evaluate(argv, cwd or workspace, workspace, caps)


ALLOWED = [
    (["python", "-m", "pytest", "-q"], "test"),
    (["python3", "-m", "pytest", "tests/test_a.py::test_x[a/b]"], "test"),
    (["python3.12", "-m", "pytest"], "test"),
    (["python.exe", "-m", "pytest"], "test"),
    (["PYTHON", "-m", "unittest", "discover"], "test"),
    (["py", "-m", "mypy", "src"], "typecheck"),
    (["python", "--version"], "inspect"),
    (["python", "-m", "pip", "list"], "inspect"),
    (["pytest", "-k", "not slow and fast", "-x"], "test"),
    (["pytest", "--rootdir=tests"], "test"),
    (["ruff", "check", "src"], "lint"),
    (["ruff", "format", "--check", "."], "lint"),
    (["black", "--check", "."], "lint"),
    (["mypy", "src"], "typecheck"),
    (["flake8", "src"], "lint"),
    (["npm", "test"], "test"),
    (["npm", "run", "lint"], "lint"),
    (["npm", "run", "test"], "test"),
    (["pnpm", "run", "build"], "build"),
    (["npm.cmd", "--version"], "inspect"),
    (["node", "--version"], "inspect"),
    (["tsc", "--noEmit"], "typecheck"),
    (["eslint", "src"], "lint"),
    (["mvn", "-q", "test"], "test"),
    (["mvn", "-B", "-DskipTests", "compile"], "build"),
    (["cargo", "test"], "test"),
    (["cargo", "fmt", "--check"], "lint"),
    (["go", "test", "./..."], "test"),
]


@pytest.mark.parametrize("argv, category", ALLOWED)
def test_allowed_commands(policy, workspace, argv, category):
    decision = decide(policy, workspace, argv)

    assert decision.allowed, decision.reason
    assert decision.category == category


DENIED = [
    (["bash", "-c", "ls"], "shells are not available"),
    (["sh"], "shells are not available"),
    (["cmd", "/c", "dir"], "shells are not available"),
    (["powershell", "-Command", "x"], "shells are not available"),
    (["env", "python", "-m", "pytest"], "shells are not available"),
    (["rm", "-rf", "x"], "destructive"),
    (["del", "x"], "destructive"),
    (["curl", "http://example.com"], "network"),
    (["wget", "x"], "network"),
    (["ssh", "host"], "network"),
    (["git", "status"], "git.* tools"),
    (["docker", "run", "x"], "container"),
    (["python", "-c", "print(1)"], "inline code"),
    (["python", "script.py"], "only 'python -m"),
    (["python", "-m", "http.server"], "not allowed"),
    (["python", "-m", "pip", "uninstall", "x"], "shell.network"),
    (["python", "-m", "pip", "config", "set", "a", "b"], "pip subcommand"),
    (["python"], "only 'python -m"),
    (["pytest", "-p", "evil"], "plugins"),
    (["pytest", "-pevil"], "plugins"),
    (["pytest", "-o", "addopts=-p evil"], "ini options"),
    (["pytest", "--override-ini=addopts=x"], "ini options"),
    (["ruff", "check", "--fix", "."], "modifies files"),
    (["ruff", "format", "."], "--check or --diff"),
    (["black", "."], "--check or --diff"),
    (["eslint", "--fix", "src"], "modifies files"),
    (["npm", "exec", "evil"], "not allowed"),
    (["npm", "run", "evil"], "only these scripts"),
    (["npm"], "subcommand is required"),
    (["npx", "evil"], "not on the allowed"),
    (["node", "script.js"], "version"),
    (["tsc"], "--noEmit"),
    (["mvn", "exec:java"], "not allowed"),
    (["mvn", "-s", "settings.xml", "test"], "not allowed"),
    (["cargo", "run"], "only cargo"),
    (["go", "run", "x.go"], "only go"),
    (["totally-unknown"], "not on the allowed"),
    (["./pytest"], "bare command name"),
    ([r"C:\Windows\System32\cmd.exe"], "bare command name"),
    (["/usr/bin/python", "-m", "pytest"], "bare command name"),
    (["pytest", "../outside"], "leaves the workspace"),
    (["pytest", "--rootdir=/etc"], "leaves the workspace"),
    (["pytest", "/abs/path"], "leaves the workspace"),
    (["pytest", "~/x"], "home directory"),
    (["pytest", "http://evil.example/x"], "URLs"),
    (["pytest", "tests/../../outside"], "leaves the workspace"),
    ([], "empty"),
    (["pytest", "a\nb"], "control characters"),
    (["pytest", "x" * 3000], "too long"),
    (["pytest"] + ["x"] * 100, "too long"),
]


@pytest.mark.parametrize("argv, fragment", DENIED)
def test_denied_commands(policy, workspace, argv, fragment):
    decision = decide(policy, workspace, argv)

    assert not decision.allowed
    assert fragment in decision.reason, decision.reason


def test_installs_need_the_network_capability(policy, workspace):
    install = ["python", "-m", "pip", "install", "requests"]
    npm = ["npm", "install"]

    for argv in (install, npm):
        without = decide(policy, workspace, argv)
        with_cap = decide(policy, workspace, argv, {CAP_SHELL_NETWORK})

        assert not without.allowed
        assert CAP_SHELL_NETWORK in without.reason
        assert without.category == "install" and without.risk == "high"
        assert with_cap.allowed
        assert with_cap.required_capabilities == {CAP_SHELL_NETWORK}


def test_cwd_inside_a_subdirectory_resolves_path_arguments_from_there(
    policy, workspace
):
    sub = workspace / "tests"

    assert decide(policy, workspace, ["pytest", "../tests"], cwd=sub).allowed
    assert not decide(policy, workspace, ["pytest", "../../x"], cwd=sub).allowed


def test_symlink_or_junction_escape_in_arguments_is_caught(
    policy, workspace, tmp_path
):
    import os
    import subprocess

    outside = tmp_path / "outside"
    outside.mkdir()
    link = workspace / "link"

    if os.name == "nt":
        done = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True,
        )

        if done.returncode != 0:
            pytest.skip("cannot create a junction")
    else:
        os.symlink(outside, link)

    decision = decide(policy, workspace, ["pytest", "link/test_x.py"])

    assert not decision.allowed
    assert "leaves the workspace" in decision.reason


def test_timeouts_are_clamped(policy):
    assert policy.clamp_timeout(None) == policy.default_timeout
    assert policy.clamp_timeout(0) == 0.1
    assert policy.clamp_timeout(10**6) == policy.max_timeout
    assert policy.clamp_timeout(5) == 5


def test_executable_name_normalization():
    assert executable_name("Python.EXE") == "python"
    assert executable_name("C:\\x\\npm.cmd") == "npm"
    assert executable_name("/usr/bin/pytest") == "pytest"


def test_an_empty_policy_denies_everything(workspace):
    decision = decide(CommandPolicy(), workspace, ["python", "--version"])

    assert not decision.allowed
    assert Path(workspace).exists()
