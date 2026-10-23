import shutil
from pathlib import Path

import pytest

from execution.executor import execute_task
from execution.gateway import ExecutionGateway
from execution.gateway.capabilities import (
    CAP_WORKTREE_DISCARD,
    CAP_WORKTREE_MANAGE,
)
from execution.gateway.tools import DEFAULT_CAPABILITIES, default_tools
from execution.tests.gitfixtures import git, init_repo, make_gateway, run, write
from execution.worktrees.manager import WorktreeManager

MANAGE = DEFAULT_CAPABILITIES | {CAP_WORKTREE_MANAGE}
MANAGE_AND_DISCARD = MANAGE | {CAP_WORKTREE_DISCARD}


@pytest.fixture
def repo(tmp_path):
    return init_repo(tmp_path / "repo")


# --------------------------------------------------------- gateway tools


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action_type, payload",
    [
        ("WORKTREE_CREATE", {"task_id": "t1"}),
        ("WORKTREE_REMOVE", {"worktree_id": "wt-x"}),
        ("WORKTREE_RECONCILE", {}),
    ],
)
async def test_worktree_management_is_denied_by_default(
    repo, action_type, payload
):
    result = await run(make_gateway(repo), action_type, payload)

    assert result.error_category == "PERMISSION_DENIED"
    assert not (repo / ".aseos_worktrees").exists()


@pytest.mark.asyncio
async def test_create_list_remove_through_the_gateway(repo):
    gateway = make_gateway(repo, MANAGE)

    created = await run(gateway, "WORKTREE_CREATE", {"task_id": "t1"})
    listed = await run(gateway, "WORKTREE_LIST")
    existed = Path(created.data["path"]).is_dir()
    removed = await run(
        gateway,
        "WORKTREE_REMOVE",
        {"worktree_id": created.data["worktree_id"]},
    )

    assert created.success is True, created.stderr
    assert created.tool == "git.worktree.create"
    assert created.data["owner"] == "act-1"
    assert existed is True
    item = listed.data["worktrees"][0]
    assert item["task_id"] == "t1"
    assert item["assessment"]["safe_to_remove"] is True
    assert removed.success is True, removed.stderr
    assert not Path(created.data["path"]).exists()


@pytest.mark.asyncio
async def test_create_tool_validates_task_id_and_duplicates(repo):
    gateway = make_gateway(repo, MANAGE)

    bad = await run(gateway, "WORKTREE_CREATE", {"task_id": "../escape"})
    await run(gateway, "WORKTREE_CREATE", {"task_id": "t1"})
    duplicate = await run(gateway, "WORKTREE_CREATE", {"task_id": "t1"})

    assert bad.error_category == "INVALID_ARGUMENTS"
    assert duplicate.error_category == "GIT_FAILURE"


@pytest.mark.asyncio
async def test_the_model_cannot_discard_unreviewed_work(repo):
    gateway = make_gateway(repo, MANAGE)
    created = await run(gateway, "WORKTREE_CREATE", {"task_id": "t1"})
    path = Path(created.data["path"])
    write(path, "a.txt", "valuable\n")
    worktree_id = created.data["worktree_id"]

    refused = await run(
        gateway, "WORKTREE_REMOVE", {"worktree_id": worktree_id}
    )
    self_authorized = await run(
        gateway,
        "WORKTREE_REMOVE",
        {"worktree_id": worktree_id, "discard_unreviewed": True},
    )

    assert refused.error_category == "PERMISSION_DENIED"
    assert refused.data["blockers"] == ["uncommitted changes"]
    assert self_authorized.error_category == "PERMISSION_DENIED"
    assert "worktree.discard" in self_authorized.stderr
    assert (path / "a.txt").read_text() == "valuable\n"


@pytest.mark.asyncio
async def test_trusted_caller_can_grant_discard(repo):
    gateway = make_gateway(repo, MANAGE_AND_DISCARD)
    created = await run(gateway, "WORKTREE_CREATE", {"task_id": "t1"})
    write(Path(created.data["path"]), "a.txt", "valuable\n")

    removed = await run(
        gateway,
        "WORKTREE_REMOVE",
        {
            "worktree_id": created.data["worktree_id"],
            "discard_unreviewed": True,
        },
    )

    assert removed.success is True, removed.stderr
    assert Path(removed.data["backup"]["files"]["patch"]).is_file()


@pytest.mark.asyncio
async def test_reconcile_through_the_gateway(repo):
    gateway = make_gateway(repo, MANAGE)
    await run(gateway, "WORKTREE_CREATE", {"task_id": "t1"})

    result = await run(gateway, "WORKTREE_RECONCILE")

    assert result.success is True, result.stderr
    assert len(result.data["ok"]) == 1


@pytest.mark.asyncio
async def test_tools_refuse_to_run_from_inside_a_worktree(repo):
    manager = WorktreeManager(repo)
    record = manager.create("t1")
    inner = make_gateway(Path(record.path), MANAGE)

    result = await run(inner, "WORKTREE_CREATE", {"task_id": "nested"})

    assert result.error_category == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_defining_flow_main_checkout_untouched(repo):
    """Create a task worktree, change it through the gateway, keep main clean."""

    main = make_gateway(repo, MANAGE)
    created = await run(main, "WORKTREE_CREATE", {"task_id": "fix-a"})
    task = ExecutionGateway(
        created.data["path"],
        tools=default_tools(),
        granted_capabilities=MANAGE,
    )
    patch = "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-one\n+uno\n"

    applied = await run(task, "APPLY_PATCH", {"patch": patch})
    diff = await run(
        task,
        "GIT_DIFF",
        {"mode": "base", "base": created.data["base_commit"]},
    )
    listed = await run(main, "WORKTREE_LIST")

    assert applied.success is True, applied.stderr
    assert "+uno" in diff.stdout
    assert (repo / "a.txt").read_text() == "one\n"
    assert git(repo, "status", "--porcelain") == ""
    assert listed.data["worktrees"][0]["assessment"]["dirty"] is True


# ----------------------------------------------------------- execute_task


def fixture_repo(tmp_path):
    source = Path("fixtures/repositories/calculator").resolve()
    target = tmp_path / "calculator"
    shutil.copytree(source, target)
    return str(target)


GOOD_PATCH = {
    "file": "calculator.py",
    "old": "return a * b",
    "new": "return a / b",
}


def test_failed_patch_leaves_nothing_behind(tmp_path):
    repo = fixture_repo(tmp_path)

    result = execute_task(
        repo,
        "bad-patch",
        {"file": "calculator.py", "old": "no such text", "new": "x"},
    )

    assert result["status"] == "ERROR"
    assert result["worktree"]["preserved"] is False
    assert result["worktree"]["status"] == "removed"
    assert not Path(result["worktree"]["path"]).exists()
    assert "aseos/bad-patch" not in git(Path(repo), "branch").split()


def test_failing_tests_preserve_the_worktree_for_debugging(tmp_path):
    repo = fixture_repo(tmp_path)

    result = execute_task(
        repo,
        "wrong-fix",
        {**GOOD_PATCH, "new": "return a - b"},
    )

    assert result["status"] == "FAIL"
    assert result["worktree"]["status"] == "failed"
    assert result["worktree"]["preserved"] is True
    assert "return a - b" in result["diff"]
    manager = WorktreeManager(repo)
    record = manager.get(result["worktree"]["id"])
    assert record.status == "failed"
    assert manager.assess(record)["unreviewed"] is True


def test_successful_run_keeps_the_change_and_the_main_checkout_clean(tmp_path):
    repo = fixture_repo(tmp_path)

    result = execute_task(repo, "good-fix", GOOD_PATCH)

    assert result["status"] == "PASS"
    assert result["worktree"]["preserved"] is True
    assert (Path(result["worktree"]["path"]) / "calculator.py").read_text(
        encoding="utf-8"
    ).count("return a / b") == 1
    assert "return a * b" in (Path(repo) / "calculator.py").read_text(
        encoding="utf-8"
    )
    assert git(Path(repo), "status", "--porcelain") == ""


def test_diff_includes_new_untracked_files(tmp_path, monkeypatch):
    repo = fixture_repo(tmp_path)

    def patch_and_add_file(worktree, patch):
        result = real_apply(worktree, patch)
        (Path(worktree) / "extra.py").write_text("x = 1\n", encoding="utf-8")
        return result

    import execution.executor as executor

    real_apply = executor.apply_patch
    monkeypatch.setattr(executor, "apply_patch", patch_and_add_file)

    result = execute_task(repo, "with-new-file", GOOD_PATCH)

    assert "+++ b/extra.py" in result["diff"]


def test_keep_false_still_refuses_to_lose_work(tmp_path):
    repo = fixture_repo(tmp_path)

    result = execute_task(repo, "keep-false", GOOD_PATCH, keep_worktree=False)

    assert result["worktree"]["preserved"] is True
    assert "unreviewed" in result["worktree"]["note"]
    assert Path(result["worktree"]["path"]).is_dir()


def test_keep_true_preserves_even_a_clean_worktree(tmp_path):
    repo = fixture_repo(tmp_path)

    result = execute_task(
        repo,
        "keep-true",
        {"file": "calculator.py", "old": "nope", "new": "x"},
        keep_worktree=True,
    )

    assert result["worktree"]["preserved"] is True
    assert Path(result["worktree"]["path"]).is_dir()


def test_running_the_same_task_id_twice_is_refused(tmp_path):
    repo = fixture_repo(tmp_path)
    execute_task(repo, "dup", GOOD_PATCH)

    with pytest.raises(Exception) as info:
        execute_task(repo, "dup", GOOD_PATCH)

    assert "already has a worktree" in str(info.value.message)


def test_unexpected_error_marks_the_worktree_failed_and_keeps_it(
    tmp_path, monkeypatch
):
    repo = fixture_repo(tmp_path)
    import execution.executor as executor

    def boom(worktree):
        raise RuntimeError("test runner exploded")

    monkeypatch.setattr(executor, "run_tests", boom)

    with pytest.raises(RuntimeError):
        execute_task(repo, "boom", GOOD_PATCH)

    manager = WorktreeManager(repo)
    record = manager.find_live("boom")
    assert record.status == "failed"
    assert Path(record.path).is_dir()
    assert "exploded" in record.note


def test_legacy_helpers_still_work_and_are_safe(tmp_path):
    from execution.git.worktree import create_worktree, remove_worktree

    repo = init_repo(tmp_path / "legacy")
    path = Path(create_worktree(str(repo), "legacy-task"))
    write(path, "a.txt", "work\n")

    with pytest.raises(Exception):
        remove_worktree(str(repo), "legacy-task")

    assert path.is_dir()

    remove_worktree(str(repo), "legacy-task", discard_confirmed=True)

    assert not path.exists()
    remove_worktree(str(repo), "legacy-task")  # no live worktree: no-op
