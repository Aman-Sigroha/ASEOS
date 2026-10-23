"""Failure injection through the public gateway: things that go wrong in real
environments, and what the caller is told when they do.

Earlier phases injected failures at exact points with real process deaths
(`test_concurrency_and_crash.py`) and at the store/parser level. This file
covers the remaining items of the qualification list end to end, and the last
test pins down where every item on that list is covered."""

import asyncio
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from execution.commands.policy import CommandPolicy, CommandRule, Match
from execution.gateway import ExecutionGateway
from execution.gateway.capabilities import CAP_SHELL_RUN, CAP_WORKTREE_MANAGE, DEFAULT_CAPABILITIES
from execution.gateway.command_tools import RunCommandTool
from execution.gateway.tools import default_tools
from execution.tests import qualification_repos as repos
from execution.tests.gitfixtures import git, init_repo, write
from execution.worktrees.manager import WorktreeManager
from runtime.schemas.action import Action

PY_POLICY = CommandPolicy(
    rules=[CommandRule("py", frozenset({"python"}), lambda args: Match("build"))]
)
CAPS = DEFAULT_CAPABILITIES | {CAP_SHELL_RUN, CAP_WORKTREE_MANAGE, "worktree.discard"}


def gateway(root, caps=CAPS):
    tools = [t for t in default_tools() if t.spec.name != "shell.run"] + [RunCommandTool(policy=PY_POLICY)]
    return ExecutionGateway(str(root), tools=tools, granted_capabilities=caps, artifact_store=False)


def act(gw, action_type, payload=None, action_id="f-1"):
    return asyncio.run(gw.execute(Action(id=action_id, type=action_type, payload=payload or {})))


def sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_read_only(path: Path):
    path.chmod(stat.S_IREAD)


def make_writable(path: Path):
    path.chmod(stat.S_IWRITE | stat.S_IREAD)


# ------------------------------------------------------ permission failures


@pytest.fixture
def read_only_repo(tmp_path):
    repo = repos.python_project(tmp_path / "repo")
    make_read_only(repo / "calc.py")
    yield repo
    make_writable(repo / "calc.py")


def test_a_read_only_file_is_a_permission_denial_not_an_internal_error(read_only_repo):
    repo = read_only_repo
    gw = gateway(repo)
    before = sha(repo / "calc.py")

    write_result = act(gw, "WRITE", {"path": "calc.py", "content": "x\n", "expected_sha256": before})
    edit_result = act(gw, "EDIT", {"file": "calc.py", "old": "return a + b", "new": "return b + a"}, "f-2")

    for result in (write_result, edit_result):
        assert not result.success
        assert result.error_category == "PERMISSION_DENIED"
        assert result.data["os_denied"] is True
        assert result.data["approvable"] is False  # no grant can fix a read-only file
        assert result.data["disposition"]["action"] == "ESCALATE"
        assert "operating system" in result.data["disposition"]["reason"]

    assert sha(repo / "calc.py") == before
    assert [p.name for p in repo.iterdir() if p.name.endswith(".tmp")] == []  # no temp litter


def test_a_patch_that_hits_a_read_only_file_rolls_back_the_files_it_already_wrote(read_only_repo):
    repo = read_only_repo
    test_before = sha(repo / "test_calc.py")
    patch = (
        "--- a/test_calc.py\n+++ b/test_calc.py\n@@ -1 +1,2 @@\n+# header\n from calc import add, mul\n"
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a + b\n+    return b + a\n"
    )
    result = act(gateway(repo), "APPLY_PATCH", {"patch": patch})

    assert result.error_category == "PERMISSION_DENIED"
    assert result.data["rolled_back"] is True and result.data["partial"] is False
    assert sha(repo / "test_calc.py") == test_before
    assert [p.name for p in repo.iterdir() if p.name.endswith(".tmp")] == []


# ------------------------------------------------------------ cleanup failure

windows_only = pytest.mark.skipif(os.name != "nt", reason="a folder in use cannot be deleted on Windows only")


def hold_open(folder: str):
    """A process whose working directory is `folder` (locks it on Windows)."""

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], cwd=folder)
    time.sleep(0.5)
    return proc


@windows_only
def test_removing_a_worktree_that_something_is_using_fails_cleanly_and_can_be_retried(tmp_path):
    repo = repos.python_project(tmp_path / "repo")
    manager = WorktreeManager(repo)
    record = manager.create("in-use")
    main = gateway(repo)
    holder = hold_open(record.path)

    try:
        failed = act(main, "WORKTREE_REMOVE", {"worktree_id": record.worktree_id})
        assert not failed.success and failed.error_category == "GIT_FAILURE"
        assert manager.get(record.worktree_id).status == "active"  # still tracked, not "removing"
    finally:
        holder.kill()
        holder.wait()

    time.sleep(0.5)
    # Git had already unregistered it and emptied it; the retry must finish the job.
    retried = act(main, "WORKTREE_REMOVE", {"worktree_id": record.worktree_id}, "f-2")
    assert retried.success, retried.stderr
    assert manager.get(record.worktree_id).status == "removed"
    assert not Path(record.path).exists()


@windows_only
def test_a_failed_removal_never_loses_discarded_work_because_it_is_backed_up_first(tmp_path):
    repo = repos.python_project(tmp_path / "repo")
    manager = WorktreeManager(repo)
    record = manager.create("precious")
    precious = Path(record.path) / "precious.txt"
    precious.write_text("hours of work\n", encoding="utf-8")
    (Path(record.path) / "calc.py").write_text("def add(a, b):\n    return 42\n", encoding="utf-8")
    holder = hold_open(record.path)

    try:
        # Without confirmation the work protects the worktree from removal.
        refused = act(gateway(repo), "WORKTREE_REMOVE", {"worktree_id": record.worktree_id})
        assert refused.error_category == "PERMISSION_DENIED"
        assert precious.read_text() == "hours of work\n"

        # With confirmation it is backed up first; the removal then fails on the lock.
        failed = act(
            gateway(repo), "WORKTREE_REMOVE",
            {"worktree_id": record.worktree_id, "discard_unreviewed": True}, "f-2",
        )
        assert not failed.success
    finally:
        holder.kill()
        holder.wait()

    backups = manager.get(record.worktree_id).backups
    assert backups, "the unreviewed work was not backed up before the removal was attempted"
    saved = [Path(p).read_text(errors="replace") for key, p in backups.items() if Path(p).is_file()]
    assert any("hours of work" in text for text in saved) or any("return 42" in text for text in saved)


# ----------------------------------------------------- damaged state on disk


def test_a_corrupt_worktree_record_does_not_break_the_others(tmp_path):
    repo = repos.python_project(tmp_path / "repo")
    manager = WorktreeManager(repo)
    good = manager.create("good")
    bad = manager.create("bad")
    records = repo / ".git" / "aseos" / "worktrees"
    (records / f"{bad.worktree_id}.json").write_text('{"worktree_id": "tru', encoding="utf-8")
    main = gateway(repo)

    listed = act(main, "WORKTREE_LIST")
    assert listed.success, listed.stderr
    assert [w["worktree_id"] for w in listed.data["worktrees"]] == [good.worktree_id]

    report = act(main, "WORKTREE_RECONCILE", action_id="f-2")
    assert report.success
    assert report.data["corrupt_records"], "the damaged record was not reported"
    assert act(main, "WORKTREE_REMOVE", {"worktree_id": good.worktree_id}, "f-3").success


def test_git_commands_in_a_directory_that_is_not_a_repository_are_categorized(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    gw = gateway(plain)

    for action_type in ("GIT_STATUS", "GIT_DIFF", "GIT_INFO"):
        result = act(gw, action_type)
        assert not result.success
        assert result.error_category == "GIT_FAILURE", (action_type, result.error_category, result.stderr)


def test_a_damaged_git_directory_is_reported_not_hung_on_or_crashed_by(tmp_path):
    repo = repos.python_project(tmp_path / "repo")
    (repo / ".git" / "HEAD").write_text("garbage that is not a ref\n", encoding="utf-8")
    started = time.monotonic()
    result = act(gateway(repo), "GIT_STATUS")

    assert time.monotonic() - started < 30
    assert not result.success
    assert result.error_category == "GIT_FAILURE"


# --------------------------------------------------------- hostile output


def run_py(gw, code, **extra):
    return act(gw, "RUN_COMMAND", {"command": ["python", "-c", code], **extra})


def test_output_that_is_not_valid_text_comes_back_as_text(tmp_path):
    repo = repos.python_project(tmp_path / "repo")
    code = (
        "import sys; sys.stdout.buffer.write(b'ok \\xff\\xfe bad bytes \\x00 nul \\x1b[31mred\\x1b[0m\\n'); "
        "sys.stderr.buffer.write(b'err \\xc3\\x28\\n')"
    )
    result = run_py(gateway(repo), code)

    assert result.success, result.stderr
    assert "ok" in result.stdout and "bad bytes" in result.stdout
    assert isinstance(result.stdout, str) and isinstance(result.stderr, str)
    json.dumps(result.model_dump())  # the whole result is serializable for the event store


def test_a_flood_of_output_is_cut_and_the_command_is_still_reported(tmp_path):
    repo = repos.python_project(tmp_path / "repo")
    code = "import sys; [sys.stdout.write('x' * 100000 + chr(10)) for _ in range(400)]"  # 40 MB
    result = run_py(gateway(repo), code)

    assert result.success, result.stderr
    assert result.truncated
    assert len(result.stdout) < 1_000_000  # the excerpt is bounded, however much was printed


def test_a_command_that_never_ends_is_stopped_and_leaves_no_process_behind(tmp_path):
    repo = repos.python_project(tmp_path / "repo")
    pid_file = tmp_path / "pid.txt"
    code = f"import os,time; open({str(pid_file)!r},'w').write(str(os.getpid())); time.sleep(300)"
    started = time.monotonic()
    result = run_py(gateway(repo), code, timeout_seconds=3)

    assert result.error_category == "TIMEOUT"
    assert time.monotonic() - started < 60
    from execution.recovery.liveness import identity_of

    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 15

    while identity_of(pid) is not None and time.monotonic() < deadline:
        time.sleep(0.2)

    assert identity_of(pid) is None


def test_a_command_the_policy_forbids_is_a_replan_not_an_approval_request(tmp_path):
    repo = repos.python_project(tmp_path / "repo")
    result = act(gateway(repo), "RUN_COMMAND", {"command": ["rm", "-rf", "."]})

    assert result.error_category == "PERMISSION_DENIED"
    assert result.data["policy_refused"] is True and result.data["not_started"] is True
    assert result.data["approvable"] is False
    assert result.data["disposition"]["action"] == "REPLAN"
    assert (repo / "calc.py").exists()


# ------------------------------------------- where each qualification item lives

COVERAGE = {
    "invalid or stale patch": [
        "test_qualification_repos.py::test_a_patch_made_against_an_older_file_is_a_conflict_that_changes_nothing",
        "test_qualification_repos.py::test_a_multi_file_patch_with_one_conflict_rolls_everything_back",
        "test_patch_tools.py::test_missing_target_file_is_a_conflict",
    ],
    "missing executable": [
        "test_shell_tool.py::test_missing_program_is_a_process_failure",
        "test_process_runner.py::test_missing_executable",
    ],
    "timeout": [
        "test_failure_injection.py::test_a_command_that_never_ends_is_stopped_and_leaves_no_process_behind",
        "test_process_runner.py::test_timeout_kills_the_whole_process_tree",
    ],
    "orphan child": [
        "test_process_runner.py::test_orphans_left_behind_by_a_finished_process_are_killed",
        "test_concurrency_and_crash.py::test_a_killed_gateway_leaves_no_runaway_process",
    ],
    "permission failure": [
        "test_failure_injection.py::test_a_read_only_file_is_a_permission_denial_not_an_internal_error",
        "test_failure_injection.py::test_a_patch_that_hits_a_read_only_file_rolls_back_the_files_it_already_wrote",
    ],
    "git lock": [
        "test_recovery_primitives.py::test_a_stuck_index_lock_is_reported_not_removed",
    ],
    "cleanup failure": [
        "test_failure_injection.py::test_removing_a_worktree_that_something_is_using_fails_cleanly_and_can_be_retried",
        "test_failure_injection.py::test_a_failed_removal_never_loses_discarded_work_because_it_is_backed_up_first",
    ],
    "test setup error": [
        "test_test_service.py::test_a_collection_error_is_an_error_not_a_failed_test",
        "test_qualification_repos.py::test_a_project_that_does_not_build_is_never_reported_as_passing",
    ],
    "corrupted report": [
        "test_test_service.py::test_a_corrupted_report_falls_back_to_the_console_and_says_so",
        "test_test_parsers.py::test_corrupt_or_hostile_reports_are_rejected",
    ],
    "artifact-store failure": [
        "test_artifact_integration.py::test_a_broken_store_never_fails_the_action_and_is_reported",
    ],
    "restart mid-run": [
        "test_concurrency_and_crash.py::test_death_halfway_through_a_patch_is_reported_as_partial_not_retried",
    ],
}


def test_every_item_of_the_failure_injection_list_has_a_test_that_exists():
    here = Path(__file__).parent
    missing = []

    for item, tests in COVERAGE.items():
        assert tests, item

        for ref in tests:
            filename, name = ref.split("::")
            source = (here / filename).read_text(encoding="utf-8")

            if not re.search(rf"^(async )?def {re.escape(name)}\(", source, re.M):
                missing.append(f"{item}: {ref}")

    assert not missing, "tests named in the coverage table do not exist:\n" + "\n".join(missing)
