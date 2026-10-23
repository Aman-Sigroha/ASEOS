from pathlib import Path

import pytest
from pydantic import BaseModel

from execution.gateway import (
    ExecutionGateway,
    Tool,
    ToolContext,
    ToolOutput,
    ToolSpec,
)
from execution.gateway.capabilities import (
    CAP_SHELL_NETWORK,
    CAP_SHELL_RUN,
    DEFAULT_CAPABILITIES,
)
from execution.gateway.command_tools import RunCommandArgs, RunCommandTool
from execution.gateway.tool import ToolError
from execution.gateway.tools import default_tools
from execution.security.levels import (
    DEV,
    ELEVATED,
    PROFILES,
    READ_ONLY,
    WORKTREE_EDIT,
    ExecutionLevel,
)
from execution.tests.gitfixtures import git, init_repo, write
from execution.worktrees.manager import WorktreeManager
from runtime.schemas.action import Action


@pytest.fixture
def workspace(tmp_path):
    root = (tmp_path / "ws").resolve()
    root.mkdir()
    return root


def gateway_for(root, profile, **kw):
    return ExecutionGateway(str(root), tools=default_tools(), profile=profile, **kw)


async def run(gateway, action_type, payload=None, action_id="a-1"):
    return await gateway.execute(
        Action(id=action_id, type=action_type, payload=payload or {})
    )


# ---------------------------------------------------------------- levels


EXPECTED_LEVELS = {
    "repo.read": "READ_ONLY",
    "repo.list_dir": "READ_ONLY",
    "repo.file_info": "READ_ONLY",
    "repo.search": "READ_ONLY",
    "patch.validate": "READ_ONLY",
    "git.status": "READ_ONLY",
    "git.diff": "READ_ONLY",
    "git.info": "READ_ONLY",
    "git.worktree.list": "READ_ONLY",
    "test.freshness": "READ_ONLY",
    "artifact.read": "READ_ONLY",
    "artifact.list": "READ_ONLY",
    "patch.replace": "LOW_RISK_MUTATION",
    "repo.write": "LOW_RISK_MUTATION",
    "patch.apply": "LOW_RISK_MUTATION",
    "git.stage": "LOW_RISK_MUTATION",
    "git.commit": "LOW_RISK_MUTATION",
    "git.branch_create": "LOW_RISK_MUTATION",
    "git.worktree.create": "LOW_RISK_MUTATION",
    "git.worktree.reconcile": "LOW_RISK_MUTATION",
    "test.run": "DEV_EXECUTION",
    "shell.run": "DEV_EXECUTION",
    "git.discard": "ELEVATED",
    "git.worktree.remove": "ELEVATED",
}


def test_every_default_tool_has_an_explicit_level():
    levels = {t.spec.name: t.spec.level.name for t in default_tools()}

    assert levels == EXPECTED_LEVELS


def test_read_only_tools_are_exactly_the_read_only_level():
    for tool in default_tools():
        spec = tool.spec

        if spec.level == ExecutionLevel.READ_ONLY:
            assert spec.read_only is True, spec.name
        else:
            assert spec.read_only is False, spec.name


def test_profiles_are_ordered_and_nested():
    assert [p.max_level for p in (READ_ONLY, WORKTREE_EDIT, DEV, ELEVATED)] == sorted(
        p.max_level for p in PROFILES.values()
    )
    assert READ_ONLY.capabilities < WORKTREE_EDIT.capabilities
    assert WORKTREE_EDIT.capabilities < DEV.capabilities
    assert DEV.capabilities < ELEVATED.capabilities
    assert DEV.sandbox_required and ELEVATED.sandbox_required
    assert not WORKTREE_EDIT.sandbox_required


def test_dangerous_capabilities_appear_only_in_the_elevated_profile():
    for cap in ("fs.delete", "git.discard", "worktree.discard", "shell.network"):
        owners = [p.name for p in PROFILES.values() if cap in p.capabilities]
        assert owners == ["elevated"], cap


def test_unknown_profile_names_are_rejected(workspace):
    with pytest.raises(ValueError):
        ExecutionGateway(str(workspace), profile="root")

    assert ExecutionGateway(str(workspace), profile="read_only").profile is READ_ONLY


def test_a_profile_is_a_ceiling_for_capabilities(workspace):
    gateway = ExecutionGateway(
        str(workspace),
        tools=default_tools(),
        profile=READ_ONLY,
        granted_capabilities=ELEVATED.capabilities,
    )

    assert gateway.granted_capabilities == READ_ONLY.capabilities


def test_describe_tools_tells_planners_what_is_available(workspace):
    descriptions = {
        d["name"]: d for d in gateway_for(workspace, READ_ONLY).describe_tools()
    }

    assert descriptions["repo.read"]["available"] is True
    assert descriptions["repo.read"]["level"] == "READ_ONLY"
    assert descriptions["repo.write"]["available"] is False
    assert descriptions["shell.run"]["available"] is False
    assert descriptions["shell.run"]["capabilities"] == ["shell.run"]
    assert descriptions["shell.run"]["timeout_seconds"] > 0
    assert {d["name"] for d in descriptions.values() if d["available"]} == {
        n for n, lvl in EXPECTED_LEVELS.items()
        if lvl == "READ_ONLY" and n != "git.worktree.list"
    } | {"git.worktree.list"}


# ----------------------------------------------------------- enforcement


@pytest.mark.asyncio
async def test_read_only_profile_cannot_change_anything(workspace):
    (workspace / "a.txt").write_text("keep")
    gateway = gateway_for(workspace, READ_ONLY)

    read = await run(gateway, "READ", {"path": "a.txt"})
    write_ = await run(gateway, "WRITE", {"path": "b.txt", "content": "x"})
    edit = await run(gateway, "EDIT", {"file": "a.txt", "old": "keep", "new": "gone"})
    patch = await run(
        gateway,
        "APPLY_PATCH",
        {"patch": "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-keep\n+gone\n"},
    )
    shell = await run(gateway, "RUN_COMMAND", {"command": ["python", "--version"]})

    assert read.success is True
    for result in (write_, edit, patch, shell):
        assert result.error_category == "PERMISSION_DENIED"
        assert "disabled" in result.stderr

    assert (workspace / "a.txt").read_text() == "keep"
    assert not (workspace / "b.txt").exists()


@pytest.mark.asyncio
async def test_level_denial_names_the_level(workspace):
    gateway = ExecutionGateway(
        str(workspace), tools=default_tools(), profile=READ_ONLY,
        granted_capabilities={"fs.read", "fs.write"},
    )

    result = await run(gateway, "WRITE", {"path": "b.txt", "content": "x"})

    assert result.error_category == "PERMISSION_DENIED"
    assert "LOW_RISK_MUTATION" in result.stderr and "READ_ONLY" in result.stderr


@pytest.mark.asyncio
async def test_worktree_edit_profile_edits_but_cannot_execute_or_destroy(tmp_path):
    repo = init_repo(tmp_path / "repo")
    manager = WorktreeManager(repo)
    record = manager.create("t1")
    gateway = gateway_for(Path(record.path), WORKTREE_EDIT)
    patch = "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-one\n+uno\n"

    applied = await run(gateway, "APPLY_PATCH", {"patch": patch})
    staged = await run(gateway, "GIT_STAGE", {"paths": ["a.txt"]})
    shell = await run(gateway, "RUN_COMMAND", {"command": ["python", "--version"]})
    tests = await run(gateway, "RUN_TEST")
    discard = await run(gateway, "GIT_DISCARD", {"paths": ["b.txt"]})
    delete = await run(
        gateway,
        "APPLY_PATCH",
        {"patch": "--- a/b.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-bee\n"},
    )

    assert applied.success and staged.success, (applied.stderr, staged.stderr)
    for result in (shell, tests, discard):
        assert result.error_category == "PERMISSION_DENIED"

    assert delete.error_category == "PERMISSION_DENIED"
    assert (Path(record.path) / "b.txt").exists()


@pytest.mark.asyncio
async def test_removal_is_elevated_even_for_a_manager(tmp_path):
    repo = init_repo(tmp_path / "repo")
    manager = WorktreeManager(repo)
    record = manager.create("t1")
    gateway = gateway_for(repo, WORKTREE_EDIT)

    result = await run(gateway, "WORKTREE_REMOVE", {"worktree_id": record.worktree_id})

    assert result.error_category == "PERMISSION_DENIED"
    assert "ELEVATED" in result.stderr
    assert Path(record.path).exists()


@pytest.mark.asyncio
async def test_dev_profile_without_a_sandbox_refuses_programs(workspace):
    gateway = gateway_for(workspace, DEV)

    result = await run(gateway, "RUN_COMMAND", {"command": ["python", "--version"]})

    assert result.error_category == "PERMISSION_DENIED"
    assert "refusing to run on the host" in result.stderr


@pytest.mark.asyncio
async def test_payload_fields_cannot_raise_privileges(workspace):
    gateway = gateway_for(workspace, DEV)
    forged = {
        "command": ["python", "-m", "pip", "install", "x"],
        "level": "ELEVATED",
        "max_level": 3,
        "capabilities": [CAP_SHELL_NETWORK, "fs.delete"],
        "granted_capabilities": [CAP_SHELL_NETWORK],
        "sandbox": False,
        "require_sandbox": False,
        "profile": "elevated",
        "network": True,
    }

    result = await run(gateway, "RUN_COMMAND", forged)

    assert result.error_category == "PERMISSION_DENIED"
    assert gateway.profile is DEV
    assert CAP_SHELL_NETWORK not in gateway.granted_capabilities
    assert gateway.require_sandbox is True

    ro = gateway_for(workspace, READ_ONLY)
    result = await run(
        ro, "WRITE", {"path": "x.txt", "content": "x", "capabilities": ["fs.write"], "level": "READ_ONLY"}
    )

    assert result.error_category == "PERMISSION_DENIED"
    assert not (workspace / "x.txt").exists()


def test_a_high_risk_command_is_refused_below_elevated_even_if_granted(workspace):
    tool = RunCommandTool()
    context = ToolContext(
        workspace=workspace,
        capabilities=frozenset({CAP_SHELL_RUN, CAP_SHELL_NETWORK}),
        max_level=ExecutionLevel.DEV_EXECUTION,
    )

    with pytest.raises(ToolError) as info:
        tool.run(
            context,
            RunCommandArgs(command=["python", "-m", "pip", "install", "x"]),
        )

    assert info.value.category == "PERMISSION_DENIED"
    assert "ELEVATED" in info.value.message


class _Anything(BaseModel):
    pass


class _Unclassified(Tool):
    spec = ToolSpec(
        name="custom.thing",
        action_type="READ",
        description="a new tool nobody has assessed",
        args_model=_Anything,
        capabilities=frozenset(),
        read_only=True,
    )

    def run(self, context, args):
        return ToolOutput(stdout="ran")


@pytest.mark.asyncio
async def test_unclassified_tools_default_to_the_highest_level(workspace):
    assert _Unclassified.spec.level == ExecutionLevel.ELEVATED

    restricted = ExecutionGateway(
        str(workspace), tools=[_Unclassified()], profile=DEV, sandbox=object()
    )
    unrestricted = ExecutionGateway(str(workspace), tools=[_Unclassified()])

    denied = await run(restricted, "READ")
    allowed = await run(unrestricted, "READ")

    assert denied.error_category == "PERMISSION_DENIED"
    assert allowed.success is True


# -------------------------------------------------- cross-task isolation


@pytest.mark.asyncio
async def test_a_task_cannot_touch_another_tasks_worktree(tmp_path):
    repo = init_repo(tmp_path / "repo")
    manager = WorktreeManager(repo)
    a, b = manager.create("task-a"), manager.create("task-b")
    path_a, path_b = Path(a.path), Path(b.path)
    write(path_b, "b.txt", "task B private work\n")
    gateway_a = gateway_for(path_a, WORKTREE_EDIT)
    sibling = f"../{path_b.name}/b.txt"

    attempts = {
        "read relative": await run(gateway_a, "READ", {"path": sibling}),
        "read absolute": await run(gateway_a, "READ", {"path": str(path_b / "b.txt")}),
        "info": await run(gateway_a, "FILE_INFO", {"path": sibling}),
        "list": await run(gateway_a, "LIST_DIR", {"path": f"../{path_b.name}"}),
        "write": await run(gateway_a, "WRITE", {"path": sibling, "content": "pwn"}),
        "patch": await run(
            gateway_a,
            "APPLY_PATCH",
            {"patch": f"--- a/{sibling}\n+++ b/{sibling}\n@@ -1 +1 @@\n-task B private work\n+pwned\n"},
        ),
        "diff path": await run(gateway_a, "GIT_DIFF", {"paths": [sibling]}),
        "stage": await run(
            ExecutionGateway(str(path_a), tools=default_tools(), profile=WORKTREE_EDIT),
            "GIT_STAGE",
            {"paths": [sibling]},
        ),
    }

    for name, result in attempts.items():
        assert result.success is False, name
        assert "private work" not in result.stdout + result.stderr, name

    assert (path_b / "b.txt").read_text() == "task B private work\n"
    assert git(path_b, "status", "--porcelain").strip() == "M b.txt"


@pytest.mark.asyncio
async def test_search_and_git_in_one_task_never_see_another(tmp_path):
    repo = init_repo(tmp_path / "repo")
    manager = WorktreeManager(repo)
    a, b = manager.create("task-a"), manager.create("task-b")
    write(Path(b.path), "only_in_b.py", "needle_from_b = 1\n")
    write(Path(a.path), "only_in_a.py", "needle_from_a = 1\n")
    gateway_a = gateway_for(Path(a.path), WORKTREE_EDIT)

    search = await run(gateway_a, "SEARCH", {"query": "needle_from"})
    status = await run(gateway_a, "GIT_STATUS")
    diff = await run(gateway_a, "GIT_DIFF")

    assert "needle_from_a" in search.stdout
    assert "needle_from_b" not in search.stdout
    assert status.data["untracked"] == ["only_in_a.py"]
    assert "only_in_b" not in diff.stdout + str(status.data)


@pytest.mark.asyncio
async def test_commands_cannot_name_another_workspace(tmp_path):
    repo = init_repo(tmp_path / "repo")
    manager = WorktreeManager(repo)
    a, b = manager.create("task-a"), manager.create("task-b")
    gateway_a = ExecutionGateway(
        str(a.path),
        tools=default_tools(),
        granted_capabilities=DEFAULT_CAPABILITIES | {CAP_SHELL_RUN},
    )

    by_arg = await run(
        gateway_a, "RUN_COMMAND", {"command": ["pytest", f"../{Path(b.path).name}"]}
    )
    by_cwd = await run(
        gateway_a,
        "RUN_COMMAND",
        {"command": ["python", "--version"], "cwd": f"../{Path(b.path).name}"},
    )
    by_flag = await run(
        gateway_a, "RUN_COMMAND", {"command": ["pytest", f"--rootdir={b.path}"]}
    )

    assert by_arg.error_category == "PERMISSION_DENIED"
    assert by_cwd.error_category == "PERMISSION_DENIED"
    assert by_flag.error_category == "PERMISSION_DENIED"
    assert "leaves the workspace" in by_arg.stderr + by_flag.stderr


@pytest.mark.asyncio
async def test_worktree_tools_cannot_be_used_from_inside_a_task(tmp_path):
    repo = init_repo(tmp_path / "repo")
    manager = WorktreeManager(repo)
    a, b = manager.create("task-a"), manager.create("task-b")
    gateway_a = ExecutionGateway(
        str(a.path), tools=default_tools(), profile=ELEVATED, sandbox=object(),
        granted_capabilities=ELEVATED.capabilities,
    )

    remove = await run(
        gateway_a, "WORKTREE_REMOVE", {"worktree_id": b.worktree_id, "discard_unreviewed": True}
    )
    listing = await run(gateway_a, "WORKTREE_LIST")

    assert remove.error_category == "PERMISSION_DENIED"
    assert listing.success is False
    assert Path(b.path).exists()
