"""Concurrency control and crash recovery, with real processes that really die."""

import asyncio
import threading
import time
from pathlib import Path

import pytest

from execution.artifacts import ArtifactStore, Owner
from execution.gateway import ExecutionGateway
from execution.gateway.tool import Tool, ToolContext, ToolOutput, ToolSpec
from execution.git.repo import GitError
from execution.recovery.lease import read_lease
from execution.recovery.liveness import identity_of
from execution.recovery.ops import OperationsLog
from execution.recovery.reconcile import acknowledge
from execution.security.levels import ExecutionLevel
from execution.tests.crash_helpers import (
    dead_pid,
    make_gateway,
    run_crashing_child,
    start_child,
    wait_for,
)
from execution.tests.gitfixtures import git, init_repo, write
from execution.worktrees.manager import WorktreeManager
from pydantic import BaseModel
from runtime.schemas.action import Action

TWO_FILE_PATCH = (
    "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1,2 @@\n one\n+two\n"
    "--- a/b.txt\n+++ b/b.txt\n@@ -1 +1,2 @@\n bee\n+buzz\n"
)


@pytest.fixture
def repo(tmp_path):
    return init_repo(tmp_path / "repo")


@pytest.fixture
def dirs(tmp_path):
    return tmp_path / "state", tmp_path / "store"


def gateway(repo, dirs, **kw):
    return make_gateway(repo, dirs[0], dirs[1], **kw)


async def act(gw, action_type, payload, action_id="a-1"):
    return await gw.execute(Action(id=action_id, type=action_type, payload=payload))


def crash(repo, dirs, mode):
    (repo.parent / "change.patch").write_text(TWO_FILE_PATCH, encoding="utf-8")
    done = run_crashing_child(repo, dirs[0], dirs[1], mode)
    assert done.returncode == 7, (done.returncode, done.stdout, done.stderr)


def only_interrupted(report):
    [op] = report["operations"]["interrupted"]
    return op, op["assessment"]


# --------------------------------------------------- serialized mutations


class _NoArgs(BaseModel):
    pass


class _Timeline:
    def __init__(self):
        self.events = []
        self.lock = threading.Lock()

    def mark(self, event):
        with self.lock:
            self.events.append((event, time.monotonic()))


def slow_tool(action_type, read_only, timeline, seconds=0.3):
    class Slow(Tool):
        spec = ToolSpec(
            name=f"test.slow_{action_type.lower()}",
            action_type=action_type,
            description="sleeps",
            args_model=_NoArgs,
            capabilities=frozenset(),
            read_only=read_only,
            level=ExecutionLevel.READ_ONLY if read_only else ExecutionLevel.LOW_RISK_MUTATION,
            timeout_seconds=30,
        )

        def run(self, context: ToolContext, args):
            timeline.mark("start")
            time.sleep(seconds)
            timeline.mark("end")
            return ToolOutput(stdout="done")

    return Slow()


def overlapped(timeline):
    depth, peak = 0, 0

    for event, _ in sorted(timeline.events, key=lambda e: e[1]):
        depth += 1 if event == "start" else -1
        peak = max(peak, depth)

    return peak > 1


@pytest.mark.asyncio
async def test_state_changing_actions_on_one_workspace_run_one_at_a_time(repo, dirs):
    timeline = _Timeline()
    gw = ExecutionGateway(
        str(repo), tools=[slow_tool("WRITE", False, timeline)],
        granted_capabilities=set(), artifact_store=False, state_dir=dirs[0],
    )

    results = await asyncio.gather(*(act(gw, "WRITE", {}, f"w-{n}") for n in range(4)))

    assert all(r.success for r in results) and len(timeline.events) == 8
    assert not overlapped(timeline)


@pytest.mark.asyncio
async def test_reads_are_not_serialized(repo, dirs):
    timeline = _Timeline()
    gw = ExecutionGateway(
        str(repo), tools=[slow_tool("READ", True, timeline)],
        granted_capabilities=set(), artifact_store=False, state_dir=dirs[0],
    )

    await asyncio.gather(*(act(gw, "READ", {}, f"r-{n}") for n in range(4)))

    assert overlapped(timeline)


@pytest.mark.asyncio
async def test_waiting_for_another_change_times_out_cleanly(repo, dirs):
    timeline = _Timeline()
    gw = ExecutionGateway(
        str(repo), tools=[slow_tool("WRITE", False, timeline, seconds=1.0)],
        granted_capabilities=set(), artifact_store=False, state_dir=dirs[0],
        mutation_wait_seconds=0.2,
    )

    first, second = await asyncio.gather(act(gw, "WRITE", {}, "w-1"), act(gw, "WRITE", {}, "w-2"))

    assert first.success
    assert not second.success and second.error_category == "TIMEOUT"
    assert "Waited" in second.stderr and len(timeline.events) == 2  # the second never ran


# ------------------------------------------------------------------ leases


@pytest.fixture
def managed(tmp_path):
    """A repository whose path is inside the managed worktree area."""

    return init_repo(tmp_path / ".aseos_worktrees" / "task-1")


@pytest.mark.asyncio
async def test_a_managed_worktree_belongs_to_one_task_at_a_time(managed, dirs):
    first = gateway(managed, dirs, task_id="task-1")
    second = gateway(managed, dirs, task_id="task-1b")

    assert (await act(first, "WRITE", {"path": "x.txt", "content": "1\n"}, "w-1")).success
    blocked = await act(second, "WRITE", {"path": "y.txt", "content": "2\n"}, "w-2")
    read = await act(second, "READ", {"path": "a.txt"}, "r-1")

    assert not blocked.success and blocked.error_category == "WORKSPACE_UNAVAILABLE"
    assert blocked.data["lease_conflict"] is True
    assert blocked.data["holder"]["owner_id"] == first.owner_id
    assert blocked.data["holder"]["task_id"] == "task-1"
    assert not (managed / "y.txt").exists()
    assert read.success  # reading is never blocked

    assert await first.shutdown() is True  # releases the lease
    assert (await act(second, "WRITE", {"path": "y.txt", "content": "2\n"}, "w-3")).success


@pytest.mark.asyncio
async def test_leases_are_off_for_ordinary_workspaces_unless_requested(repo, dirs):
    a, b = gateway(repo, dirs), gateway(repo, dirs)

    assert (await act(a, "WRITE", {"path": "1.txt", "content": "1\n"}, "w-1")).success
    assert (await act(b, "WRITE", {"path": "2.txt", "content": "2\n"}, "w-2")).success
    assert read_lease(dirs[0], repo) is None

    forced = gateway(repo, dirs, lease=True)
    other = gateway(repo, dirs, lease=True)
    assert (await act(forced, "WRITE", {"path": "3.txt", "content": "3\n"}, "w-3")).success
    refused = await act(other, "WRITE", {"path": "4.txt", "content": "4\n"}, "w-4")
    assert refused.error_category == "WORKSPACE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_a_dead_owners_lease_never_blocks_its_successor(managed, dirs):
    # A real process takes the lease, then dies without releasing it.
    (managed.parent / "change.patch").write_text(TWO_FILE_PATCH)
    crashed = run_crashing_child(managed, dirs[0], dirs[1], "write_all")
    assert crashed.returncode == 7
    assert read_lease(dirs[0], managed)["stale"] is True

    successor = gateway(managed, dirs)

    assert (await act(successor, "WRITE", {"path": "later.txt", "content": "ok\n"}, "w-1")).success
    assert read_lease(dirs[0], managed)["owner_id"] == successor.owner_id


# ------------------------------------------------------------ the journal


@pytest.mark.asyncio
async def test_a_running_command_is_in_the_table_with_its_process(repo, dirs):
    gw = gateway(repo, dirs)
    log = OperationsLog(dirs[0])
    task = asyncio.create_task(act(
        gw, "RUN_COMMAND",
        {"command": ["python", "-c", "import time; time.sleep(300)"]}, "long-1",
    ))

    record = await asyncio.to_thread(wait_for, lambda: next(
        (r for r in log.list(repo) if r.process), None))

    assert record.action_id == "long-1" and record.tool == "shell.run"
    assert record.task_id is None and record.owner_id == gw.owner_id
    assert record.head_before == git(repo, "rev-parse", "HEAD").strip()
    child = record.process["pid"]
    assert identity_of(child) is not None and record.process["run_id"].startswith("run-")

    assert gw.cancel("long-1")
    result = await task

    assert not result.success and result.error_category == "CANCELLED"
    assert log.list(repo) == []  # the operation ended: nothing left behind
    wait_for(lambda: identity_of(child) is None)


@pytest.mark.asyncio
async def test_finished_and_failed_actions_leave_no_journal_entry(repo, dirs):
    gw = gateway(repo, dirs)

    ok = await act(gw, "WRITE", {"path": "n.txt", "content": "x\n"}, "ok")
    bad = await act(gw, "WRITE", {"path": "a.txt", "content": "clobber\n"}, "bad")  # needs expected_sha256

    assert ok.success and not bad.success
    assert OperationsLog(dirs[0]).list(repo) == []
    assert (repo / "a.txt").read_text() == "one\n"


@pytest.mark.asyncio
async def test_without_a_writable_journal_a_required_intent_blocks_the_change(repo, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    strict = gateway(repo, (blocker, tmp_path / "store"), require_journal=True)

    result = await act(strict, "WRITE", {"path": "n.txt", "content": "x\n"})

    assert not result.success and result.error_category == "RESOURCE_LIMIT"
    assert "refusing to change the workspace" in result.stderr
    assert not (repo / "n.txt").exists()  # nothing was done without a recorded intent


@pytest.mark.asyncio
async def test_without_a_writable_journal_the_default_still_works_and_says_so(repo, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    lenient = gateway(repo, (blocker, tmp_path / "store"))

    result = await act(lenient, "WRITE", {"path": "n.txt", "content": "x\n"})

    assert result.success and (repo / "n.txt").exists()
    assert any(e.startswith("journal:") for e in result.data["artifact_errors"])


# ------------------------------------------- crash injection: real deaths


@pytest.mark.asyncio
async def test_death_before_any_write_is_safe_to_retry(repo, dirs):
    crash(repo, dirs, "patch_none")

    report = gateway(repo, dirs).recover()
    op, assessment = only_interrupted(report)

    assert op["tool"] == "patch.apply" and op["action_id"] == "doomed-1"
    assert assessment["outcome"] == "not_applied" and assessment["resumable"] is True
    assert (repo / "a.txt").read_text() == "one\n" and report["needs_attention"] is True


@pytest.mark.asyncio
async def test_death_halfway_through_a_patch_is_reported_as_partial_not_retried(repo, dirs):
    crash(repo, dirs, "patch_partial")

    report = gateway(repo, dirs).recover()
    _, assessment = only_interrupted(report)

    assert assessment["outcome"] == "partial" and assessment["resumable"] is False
    assert assessment["files"] == {"a.txt": "applied", "b.txt": "unchanged"}
    assert "inspect" in assessment["recommendation"].lower()
    assert (repo / "a.txt").read_text() == "one\ntwo\n" and (repo / "b.txt").read_text() == "bee\n"


@pytest.mark.asyncio
async def test_death_after_the_patch_landed_says_do_not_run_it_again(repo, dirs):
    crash(repo, dirs, "patch_all")

    gw = gateway(repo, dirs)
    _, assessment = only_interrupted(gw.recover())

    assert assessment["outcome"] == "applied" and assessment["resumable"] is False
    assert "do not re-run" in assessment["recommendation"].lower()
    # A small diff can match an already-changed file, so a blind retry is
    # dangerous; the pre-change hashes in the report make it refuse instead.
    guarded = await act(
        gw, "APPLY_PATCH", {"patch": TWO_FILE_PATCH, "base": assessment["base"]}, "retry"
    )
    assert not guarded.success and guarded.error_category == "PATCH_CONFLICT"
    assert (repo / "a.txt").read_text() == "one\ntwo\n"
    assert (repo / "b.txt").read_text() == "bee\nbuzz\n"


@pytest.mark.asyncio
async def test_a_file_edited_after_the_crash_is_flagged_as_diverged(repo, dirs):
    crash(repo, dirs, "patch_all")
    write(repo, "a.txt", "one\ntwo\nsomeone else typed this\n")

    _, assessment = only_interrupted(gateway(repo, dirs).recover())

    assert assessment["outcome"] == "diverged" and assessment["files"]["a.txt"] == "diverged"


@pytest.mark.asyncio
async def test_death_during_a_file_write(repo, dirs):
    crash(repo, dirs, "write_all")

    _, assessment = only_interrupted(gateway(repo, dirs).recover())

    assert assessment["outcome"] == "applied" and assessment["files"] == {"new.txt": "applied"}
    assert (repo / "new.txt").read_text() == "fresh\n"


@pytest.mark.asyncio
async def test_death_around_a_commit(repo, dirs):
    write(repo, "a.txt", "one\nchanged\n")
    git(repo, "add", "a.txt")
    head = git(repo, "rev-parse", "HEAD").strip()
    crash(repo, dirs, "commit_none")
    _, before = only_interrupted(gateway(repo, dirs).recover())
    assert before["outcome"] == "not_committed" and before["resumable"] is True
    assert git(repo, "rev-parse", "HEAD").strip() == head

    # A fresh scenario where the commit did land before the process died.
    OperationsLog(dirs[0]).remove(OperationsLog(dirs[0]).list(repo)[0])
    crash(repo, dirs, "commit_done")
    _, after = only_interrupted(gateway(repo, dirs).recover())

    assert after["outcome"] == "committed" and after["resumable"] is False
    assert after["subject"] == "intended commit" and after["head_now"] != head


@pytest.mark.asyncio
async def test_a_killed_gateway_leaves_no_runaway_process(repo, dirs):
    child = start_child(repo, dirs[0], dirs[1], "sleep")
    log = OperationsLog(dirs[0])

    try:
        record = wait_for(lambda: next((r for r in log.list(repo) if r.process), None), timeout=150)
        runner = record.process["pid"]
        child.kill()  # no cleanup handlers run, like kill -9
        child.wait(timeout=30)
        gw = gateway(repo, dirs)

        report = gw.recover(terminate_orphans=True)
        _, assessment = only_interrupted(report)

        assert assessment["process"]["state"] in ("gone", "orphan_terminated")
        assert assessment["outcome"] == "unknown" and assessment["resumable"] is False
        assert assessment["head_changed"] is False
        wait_for(lambda: identity_of(runner) is None, timeout=60)
    finally:
        if child.poll() is None:
            child.kill()


# ------------------------------------------------------ reconcile behaviour


def dead_owner(log, repo, action, **changes):
    """A journal entry whose owner process no longer exists."""

    handle = log.begin(
        action_id=action, action_type="WRITE", tool="repo.write", workspace=repo,
        owner_id="gone", task_id=None, args_sha256="0" * 64, args={}, head_before=None,
    )
    record = handle.record
    record.pid, record.pid_created = dead_pid(), None

    for key, value in changes.items():
        setattr(record, key, value)

    log.save(record)
    return record


@pytest.mark.asyncio
async def test_reconcile_is_repeatable_and_changes_nothing_but_the_marking(repo, dirs):
    crash(repo, dirs, "patch_partial")
    before = {p.name: p.read_bytes() for p in repo.iterdir() if p.is_file()}
    gw = gateway(repo, dirs)

    first, second = gw.recover(), gw.recover()

    assert only_interrupted(first)[1] == only_interrupted(second)[1]
    assert OperationsLog(dirs[0]).list(repo)[0].state == "interrupted"
    assert {p.name: p.read_bytes() for p in repo.iterdir() if p.is_file()} == before


@pytest.mark.asyncio
async def test_acknowledging_removes_an_interrupted_operation(repo, dirs):
    crash(repo, dirs, "patch_none")
    gw = gateway(repo, dirs)
    op, _ = only_interrupted(gw.recover())

    assert acknowledge(OperationsLog(dirs[0]), repo, op["op_id"]) is True
    assert acknowledge(OperationsLog(dirs[0]), repo, op["op_id"]) is False
    assert gw.recover()["operations"]["interrupted"] == [] and not gw.recover()["needs_attention"]


@pytest.mark.asyncio
async def test_an_action_that_finished_but_missed_its_cleanup_is_not_called_interrupted(repo, dirs):
    log = OperationsLog(dirs[0])
    dead_owner(log, repo, "finished-1")
    dead_owner(log, repo, "died-1")
    gw = gateway(repo, dirs)
    gw.artifact_store.put(
        "{}", kind="execution_record", owner=Owner(gw.workspace_id, action_id="finished-1"),
        retention="audit",
    )

    ops = gw.recover()["operations"]

    assert ops["cleaned"] == 1
    assert [o["action_id"] for o in ops["interrupted"]] == ["died-1"]


@pytest.mark.asyncio
async def test_live_operations_are_left_alone(repo, dirs):
    log = OperationsLog(dirs[0])
    gw = gateway(repo, dirs)
    mine = log.begin(action_id="mine", action_type="WRITE", tool="repo.write", workspace=repo,
                     owner_id=gw.owner_id, task_id=None, args_sha256="0" * 64, args={}, head_before=None)
    theirs = log.begin(action_id="theirs", action_type="WRITE", tool="repo.write", workspace=repo,
                       owner_id="someone-else", task_id=None, args_sha256="0" * 64, args={}, head_before=None)

    ops = gw.recover()["operations"]

    assert [o["action_id"] for o in ops["running"]] == ["mine"]
    assert [o["action_id"] for o in ops["running_elsewhere"]] == ["theirs"]
    assert ops["interrupted"] == []
    mine.finish(), theirs.finish()


@pytest.mark.asyncio
async def test_an_operation_from_another_machine_is_judged_by_its_age(repo, dirs):
    log = OperationsLog(dirs[0])
    dead_owner(log, repo, "recent", host="another-host", updated_at=time.time())
    old = dead_owner(log, repo, "ancient", host="another-host")
    old.updated_at = time.time() - 7200
    path = log.path_of(old)
    path.write_text(old.model_dump_json())  # bypass save(), which refreshes updated_at

    ops = gateway(repo, dirs).recover()["operations"]

    assert [o["action_id"] for o in ops["running_elsewhere"]] == ["recent"]
    assert [o["action_id"] for o in ops["interrupted"]] == ["ancient"]


@pytest.mark.asyncio
async def test_recover_reports_stale_leases_and_store_problems_and_missing_worktrees(repo, dirs, tmp_path):
    manager = WorktreeManager(repo)
    record = manager.create("task-x")
    import shutil
    shutil.rmtree(record.path, ignore_errors=True)  # the worktree vanished
    gw = gateway(repo, dirs)
    gw.artifact_store._objects.joinpath("art_" + "7" * 24 + ".blob").write_bytes(b"orphan")
    crash_dir = tmp_path / "ghostws"
    crash_dir.mkdir()
    from execution.recovery.lease import WorkspaceLease
    from execution.recovery.locations import lease_name, leases_dir
    import json
    leases_dir(dirs[0]).mkdir(parents=True, exist_ok=True)
    (leases_dir(dirs[0]) / lease_name(crash_dir)).write_text(json.dumps({
        "owner_id": "ghost", "pid": dead_pid(), "host": __import__("execution.recovery.locations", fromlist=["x"]).this_host(),
        "heartbeat_at": time.time(), "ttl": 120}))

    report = gw.recover()

    assert [l["owner_id"] for l in report["leases"]["stale"]] == ["ghost"]
    assert report["artifacts"]["orphan_blobs"] == ["art_" + "7" * 24] and report["artifacts"]["clean"] is False
    assert record.worktree_id in str(report["worktrees"])  # reported as missing, nothing deleted
    assert not Path(record.path).exists()


@pytest.mark.asyncio
async def test_one_failing_section_does_not_hide_the_others(repo, dirs, monkeypatch):
    gw = gateway(repo, dirs)
    monkeypatch.setattr(gw.artifact_store, "audit", lambda **k: (_ for _ in ()).throw(OSError("disk gone")))

    report = gw.recover()

    assert "disk gone" in report["artifacts"]["error"]
    assert "interrupted" in report["operations"] and "stale" in report["leases"]


# --------------------------------- cleanup must not remove work in progress


@pytest.mark.asyncio
async def test_a_worktree_with_a_running_task_cannot_be_removed(repo):
    manager = WorktreeManager(repo)
    record = manager.create("task-live")
    gw = ExecutionGateway(
        record.path, tools=__import__("execution.tests.crash_helpers", fromlist=["x"]).tools(),
        granted_capabilities={"fs.read", "fs.write", "git.read"}, artifact_store=False, lease=True,
    )

    await act(gw, "WRITE", {"path": "work.txt", "content": "valuable\n"})

    with pytest.raises(GitError) as running:
        manager.remove(record.worktree_id, discard_confirmed=True)
    assert "running task holds this worktree" in running.value.message
    assert (Path(record.path) / "work.txt").read_text() == "valuable\n"

    assert await gw.shutdown() is True
    with pytest.raises(GitError) as unreviewed:
        manager.remove(record.worktree_id)  # no longer running, but the work is unreviewed
    assert "unreviewed" in unreviewed.value.message
    assert (Path(record.path) / "work.txt").exists()

    done = manager.remove(record.worktree_id, discard_confirmed=True)  # explicit, and backed up
    assert done["removed"] is True


@pytest.mark.asyncio
async def test_a_worktree_with_a_running_operation_cannot_be_removed(repo, dirs):
    manager = WorktreeManager(repo)
    record = manager.create("task-op")
    state = repo / ".git" / "aseos"
    log = OperationsLog(state)
    handle = log.begin(action_id="busy", action_type="RUN_COMMAND", tool="shell.run",
                       workspace=Path(record.path), owner_id="x", task_id=None,
                       args_sha256="0" * 64, args={}, head_before=None)

    with pytest.raises(GitError) as err:
        manager.remove(record.worktree_id, discard_confirmed=True)

    assert "still running" in err.value.message and err.value.data["action_id"] == "busy"
    handle.finish()
    assert manager.remove(record.worktree_id)["removed"] is True  # nothing in use, nothing to lose
