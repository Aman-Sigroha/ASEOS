import subprocess
import time

import pytest
from pydantic import BaseModel

from execution.gateway import (
    ExecutionGateway,
    Tool,
    ToolContext,
    ToolError,
    ToolOutput,
    ToolSpec,
)
from execution.gateway.tools import DEFAULT_CAPABILITIES, default_tools
from runtime.schemas.action import Action


class _Empty(BaseModel):
    pass


class _SlowTool(Tool):
    spec = ToolSpec(
        name="test.slow",
        action_type="READ",
        description="sleeps",
        args_model=_Empty,
        capabilities=frozenset(),
        read_only=True,
        timeout_seconds=0.05,
    )

    def run(self, context: ToolContext, args) -> ToolOutput:
        time.sleep(0.3)
        return ToolOutput()


class _CrashingTool(_SlowTool):
    spec = ToolSpec(
        name="test.crash",
        action_type="READ",
        description="raises",
        args_model=_Empty,
        capabilities=frozenset(),
        read_only=True,
    )

    def run(self, context: ToolContext, args) -> ToolOutput:
        raise RuntimeError("boom")


def make_gateway(tmp_path, tools=None, capabilities=DEFAULT_CAPABILITIES):
    return ExecutionGateway(
        str(tmp_path),
        tools=default_tools() if tools is None else tools,
        granted_capabilities=capabilities,
    )


def action(action_type, payload=None, action_id="a-1"):
    return Action(id=action_id, type=action_type, payload=payload or {})


def test_workspace_must_exist(tmp_path):
    with pytest.raises(ValueError):
        ExecutionGateway(str(tmp_path / "missing"))


def test_duplicate_registration_rejected(tmp_path):
    gateway = make_gateway(tmp_path)

    with pytest.raises(ValueError):
        gateway.register(default_tools()[0])


def test_default_tools_declare_canonical_names_and_read_only_flags():
    specs = {t.spec.action_type: t.spec for t in default_tools()}

    assert specs["READ"].name == "repo.read"
    assert specs["SEARCH"].name == "repo.search"
    assert specs["EDIT"].name == "patch.apply"
    assert specs["RUN_TEST"].name == "test.run"
    assert specs["GIT_DIFF"].name == "git.diff"
    assert specs["LIST_DIR"].name == "repo.list_dir"
    assert specs["FILE_INFO"].name == "repo.file_info"
    assert specs["WRITE"].name == "repo.write"
    assert specs["WRITE"].read_only is False
    assert specs["LIST_DIR"].read_only is True
    assert specs["RUN_COMMAND"].name == "shell.run"
    assert specs["READ"].read_only is True
    assert specs["EDIT"].read_only is False


@pytest.mark.asyncio
async def test_unknown_tool_is_categorized(tmp_path):
    gateway = make_gateway(tmp_path, tools=[])

    result = await gateway.execute(action("READ", {"path": "x"}))

    assert result.success is False
    assert result.error_category == "UNKNOWN_TOOL"
    assert result.tool is None


@pytest.mark.asyncio
async def test_invalid_arguments_are_categorized(tmp_path):
    gateway = make_gateway(tmp_path)

    result = await gateway.execute(action("READ", {"path": "  "}))

    assert result.success is False
    assert result.error_category == "INVALID_ARGUMENTS"
    assert result.tool == "repo.read"
    assert "path" in result.stderr


@pytest.mark.asyncio
async def test_missing_required_argument_is_invalid(tmp_path):
    gateway = make_gateway(tmp_path)

    result = await gateway.execute(action("EDIT", {"file": "a.py"}))

    assert result.error_category == "INVALID_ARGUMENTS"


@pytest.mark.asyncio
async def test_run_command_denied_without_capability(tmp_path):
    gateway = make_gateway(tmp_path)

    result = await gateway.execute(
        action("RUN_COMMAND", {"command": "echo hi"})
    )

    assert result.success is False
    assert result.error_category == "PERMISSION_DENIED"
    assert "disabled" in result.stderr


@pytest.mark.asyncio
async def test_capability_can_be_withheld_from_other_tools(tmp_path):
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    gateway = make_gateway(tmp_path, capabilities={"git.read"})

    result = await gateway.execute(action("READ", {"path": "f.txt"}))

    assert result.error_category == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_path_escape_is_permission_denied(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    gateway = make_gateway(workspace)

    result = await gateway.execute(
        action("READ", {"path": "../outside.txt"})
    )

    assert result.error_category == "PERMISSION_DENIED"
    assert "secret" not in result.stdout


@pytest.mark.asyncio
async def test_read_non_utf8_file_is_invalid(tmp_path):
    (tmp_path / "bin.dat").write_bytes(b"\xff\xfe\x00\x90")
    gateway = make_gateway(tmp_path)

    result = await gateway.execute(action("READ", {"path": "bin.dat"}))

    assert result.error_category == "INVALID_ARGUMENTS"


@pytest.mark.asyncio
async def test_successful_result_carries_tool_and_duration(tmp_path):
    (tmp_path / "f.txt").write_text("hello", encoding="utf-8")
    gateway = make_gateway(tmp_path)

    result = await gateway.execute(action("READ", {"path": "f.txt"}))

    assert result.success is True
    assert result.stdout == "hello"
    assert result.tool == "repo.read"
    assert result.error_category is None
    assert result.duration_ms is not None


@pytest.mark.asyncio
async def test_patch_conflicts_are_categorized(tmp_path):
    (tmp_path / "f.py").write_text("a\na\n", encoding="utf-8")
    gateway = make_gateway(tmp_path)

    ambiguous = await gateway.execute(
        action("EDIT", {"file": "f.py", "old": "a", "new": "b"})
    )
    missing = await gateway.execute(
        action("EDIT", {"file": "f.py", "old": "zzz", "new": "b"})
    )

    assert ambiguous.error_category == "PATCH_CONFLICT"
    assert missing.error_category == "PATCH_CONFLICT"
    assert (tmp_path / "f.py").read_text(encoding="utf-8") == "a\na\n"


@pytest.mark.asyncio
async def test_successful_edit_reports_changed_files(tmp_path):
    (tmp_path / "f.py").write_text("x = 1\n", encoding="utf-8")
    gateway = make_gateway(tmp_path)

    result = await gateway.execute(
        action("EDIT", {"file": "f.py", "old": "1", "new": "2"})
    )

    assert result.success is True
    assert result.data["changed_files"] == ["f.py"]
    assert len(result.data["sha256"]) == 64


@pytest.mark.asyncio
async def test_tool_timeout_is_categorized(tmp_path):
    gateway = make_gateway(tmp_path, tools=[_SlowTool()])

    result = await gateway.execute(action("READ"))

    assert result.success is False
    assert result.error_category == "TIMEOUT"


@pytest.mark.asyncio
async def test_unexpected_exception_becomes_internal_error(tmp_path):
    gateway = make_gateway(tmp_path, tools=[_CrashingTool()])

    result = await gateway.execute(action("READ"))

    assert result.success is False
    assert result.error_category == "INTERNAL_ERROR"
    assert "RuntimeError: boom" in result.stderr


@pytest.mark.asyncio
async def test_test_failure_is_distinguished_from_pass(tmp_path):
    (tmp_path / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    gateway = make_gateway(tmp_path)

    passed = await gateway.execute(action("RUN_TEST"))
    assert passed.success is True
    assert passed.exit_code == 0

    (tmp_path / "test_bad.py").write_text(
        "def test_bad():\n    assert False\n", encoding="utf-8"
    )
    failed = await gateway.execute(action("RUN_TEST"))

    assert failed.success is False
    assert failed.error_category == "TEST_FAILURE"
    assert failed.exit_code == 1


@pytest.mark.asyncio
async def test_git_diff_through_gateway(tmp_path):
    def git(*args):
        subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, capture_output=True
        )

    git("init")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "T")
    (tmp_path / "f.txt").write_text("one\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "init")
    (tmp_path / "f.txt").write_text("two\n", encoding="utf-8")
    gateway = make_gateway(tmp_path)

    result = await gateway.execute(action("GIT_DIFF"))

    assert result.success is True
    assert "+two" in result.stdout


@pytest.mark.asyncio
async def test_git_diff_outside_repo_is_git_failure(tmp_path):
    gateway = make_gateway(tmp_path)

    result = await gateway.execute(action("GIT_DIFF"))

    # tmp_path may sit inside another repo on some machines; only assert
    # the failure shape when git actually fails.
    if not result.success:
        assert result.error_category == "GIT_FAILURE"


@pytest.mark.asyncio
async def test_search_truncation_flag(tmp_path):
    (tmp_path / "big.txt").write_text(
        "\n".join("needle" for _ in range(150)), encoding="utf-8"
    )
    gateway = make_gateway(tmp_path)

    result = await gateway.execute(action("SEARCH", {"query": "needle"}))

    assert result.success is True
    assert result.truncated is True
    assert result.data["match_count"] == 100
