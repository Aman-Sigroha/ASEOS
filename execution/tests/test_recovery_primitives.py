"""Liveness, leases, the operations journal, store audit and Git lock handling."""

import gc
import json
import os
import subprocess
import sys
import threading
import time

import pytest

from execution.artifacts import ArtifactStore, Owner, workspace_id
from execution.git import repo as git_repo
from execution.git.repo import GitError
from execution.recovery.lease import (
    LeaseConflict,
    LeaseLost,
    WorkspaceLease,
    all_leases,
    live_holder,
    read_lease,
)
from execution.recovery.liveness import identity_of, is_same_process, is_verified, terminate_tree
from execution.recovery.locations import lease_name, leases_dir, this_host
from execution.recovery.ops import JournalError, OperationsLog
from execution.tests.crash_helpers import dead_pid
from execution.tests.gitfixtures import git, init_repo, make_gateway, run, write


@pytest.fixture
def state(tmp_path):
    return tmp_path / "state"


@pytest.fixture
def ws(tmp_path):
    path = (tmp_path / "ws").resolve()
    path.mkdir()
    return path


# ------------------------------------------------------------------ liveness


def test_this_process_is_alive_and_verified():
    me = identity_of(os.getpid())

    assert me is not None and me.pid == os.getpid()
    assert is_same_process(os.getpid(), me.created)
    if me.created is not None:
        assert is_verified(os.getpid(), me.created)


def test_an_exited_process_is_not_alive():
    pid = dead_pid()

    assert identity_of(pid) is None
    assert not is_same_process(pid, None) and not is_verified(pid, 123)


@pytest.mark.parametrize("bad", [0, -5, None, "x"])
def test_nonsense_pids_are_not_alive(bad):
    assert identity_of(bad) is None


def test_a_reused_pid_with_another_creation_token_is_not_the_same_process():
    me = identity_of(os.getpid())
    if me is None or me.created is None:
        pytest.skip("no process creation token on this platform")

    assert not is_same_process(os.getpid(), me.created + 12345)
    assert not is_verified(os.getpid(), me.created + 12345)


def test_terminate_tree_kills_only_a_verified_process():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    identity = identity_of(child.pid)

    try:
        if identity is None or identity.created is None:
            pytest.skip("no process creation token on this platform")

        # A different identity is not the process we recorded: nothing is killed.
        terminate_tree(child.pid, identity.created + 1)
        assert child.poll() is None  # still running

        assert terminate_tree(child.pid, identity.created) is True
        child.wait(timeout=10)
    finally:
        if child.poll() is None:
            child.kill()


# -------------------------------------------------------------------- leases


def lease(state, ws, owner, **kw):
    return WorkspaceLease(state, ws, owner_id=owner, **kw)


def test_a_lease_records_its_owner(state, ws):
    a = lease(state, ws, "A", task_id="t1")

    a.acquire()
    held = read_lease(state, ws)

    assert held["owner_id"] == "A" and held["pid"] == os.getpid() and held["task_id"] == "t1"
    assert held["host"] == this_host() and held["owner_alive"] is True and held["stale"] is False
    assert held["workspace_id"] == workspace_id(ws)
    assert live_holder(state, ws)["owner_id"] == "A"


def test_a_second_live_owner_is_refused_with_the_holder_described(state, ws):
    holder = lease(state, ws, "A", task_id="t1")
    holder.acquire()

    with pytest.raises(LeaseConflict) as err:
        lease(state, ws, "B").acquire()

    assert err.value.holder["owner_id"] == "A" and err.value.holder["task_id"] == "t1"
    assert read_lease(state, ws)["owner_id"] == "A"  # untouched


def test_the_owner_may_reacquire_and_release(state, ws):
    a = lease(state, ws, "A")
    a.acquire()
    first = read_lease(state, ws)["heartbeat_at"]
    time.sleep(0.02)

    a.acquire()

    assert read_lease(state, ws)["heartbeat_at"] > first
    a.release()
    assert read_lease(state, ws) is None
    lease(state, ws, "B").acquire()  # now free


def test_a_lease_of_a_dead_owner_is_taken_over(state, ws):
    path = leases_dir(state) / lease_name(ws)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "owner_id": "ghost", "pid": dead_pid(), "pid_created": 1, "host": this_host(),
        "task_id": "old", "heartbeat_at": time.time(), "ttl": 120,
    }))
    assert read_lease(state, ws)["stale"] is True
    assert live_holder(state, ws) is None

    b = lease(state, ws, "B")
    b.acquire()

    assert read_lease(state, ws)["owner_id"] == "B" and b.held


def test_a_recycled_pid_does_not_keep_a_dead_owners_lease_alive(state, ws):
    me = identity_of(os.getpid())
    if me is None or me.created is None:
        pytest.skip("no process creation token on this platform")

    path = leases_dir(state) / lease_name(ws)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "owner_id": "ghost", "pid": os.getpid(), "pid_created": me.created + 999,
        "host": this_host(), "heartbeat_at": time.time(), "ttl": 120,
    }))

    assert read_lease(state, ws)["stale"] is True
    lease(state, ws, "B").acquire()


def test_a_lease_from_another_host_goes_stale_by_heartbeat_only(state, ws):
    path = leases_dir(state) / lease_name(ws)
    path.parent.mkdir(parents=True)

    def write(age):
        path.write_text(json.dumps({
            "owner_id": "remote", "pid": 1, "host": "some-other-host",
            "heartbeat_at": time.time() - age, "ttl": 120,
        }))

    write(30)
    assert read_lease(state, ws)["stale"] is False and read_lease(state, ws)["owner_alive"] is None
    with pytest.raises(LeaseConflict):
        lease(state, ws, "B").acquire()

    write(500)
    assert read_lease(state, ws)["stale"] is True
    lease(state, ws, "B").acquire()


def test_a_corrupt_lease_file_does_not_block_forever(state, ws):
    path = leases_dir(state) / lease_name(ws)
    path.parent.mkdir(parents=True)
    path.write_text("{ not json")

    mine = lease(state, ws, "B")
    mine.acquire()

    assert read_lease(state, ws)["owner_id"] == "B" and mine.held


def test_a_taken_over_owner_learns_it_lost_the_lease(state, ws):
    a = lease(state, ws, "A")
    a.acquire()
    path = leases_dir(state) / lease_name(ws)
    path.write_text(json.dumps({"owner_id": "usurper", "pid": dead_pid(), "host": this_host(),
                                "heartbeat_at": time.time(), "ttl": 120}))
    b = lease(state, ws, "B")
    b.acquire()  # usurper is dead -> B takes over

    with pytest.raises(LeaseLost):
        a.heartbeat()
    with pytest.raises(LeaseConflict):  # A no longer owns it and B is alive
        a.acquire()
    assert read_lease(state, ws)["owner_id"] == "B"


def test_exactly_one_contender_wins_a_stale_lease(state, ws):
    path = leases_dir(state) / lease_name(ws)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"owner_id": "ghost", "pid": dead_pid(), "host": this_host(),
                                "heartbeat_at": time.time(), "ttl": 120}))
    winners, losers = [], []
    barrier = threading.Barrier(8)

    def contend(n):
        barrier.wait()
        mine = lease(state, ws, f"owner-{n}")
        try:
            mine.acquire()
            winners.append(mine)
        except LeaseConflict:
            losers.append(n)

    threads = [threading.Thread(target=contend, args=(n,)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert len(winners) == 1 and len(losers) == 7
    assert read_lease(state, ws)["owner_id"] == winners[0].owner_id


def test_a_forgotten_lease_is_released_when_its_object_is_collected(state, ws):
    def scope():
        mine = lease(state, ws, "A")
        mine.acquire()

    scope()
    gc.collect()

    assert read_lease(state, ws) is None


def test_all_leases_lists_everything_with_staleness(state, tmp_path):
    other = (tmp_path / "other").resolve()
    other.mkdir()
    one = (tmp_path / "one").resolve()
    one.mkdir()
    keep = lease(state, one, "A")
    keep.acquire()
    path = leases_dir(state) / lease_name(other)
    path.write_text(json.dumps({"owner_id": "ghost", "pid": dead_pid(), "host": this_host(),
                                "heartbeat_at": time.time(), "ttl": 120}))

    found = {item["owner_id"]: item["stale"] for item in all_leases(state)}

    assert found == {"A": False, "ghost": True}


# ------------------------------------------------------------------- journal


def begin(log, ws, action="act-1", **kw):
    return log.begin(
        action_id=action, action_type="WRITE", tool="repo.write", workspace=ws,
        owner_id="A", task_id="t1", args_sha256="0" * 64, args={"path": "a"},
        head_before="abc", **kw,
    )


def test_intent_is_recorded_before_the_action_and_removed_after(state, ws):
    log = OperationsLog(state)

    handle = begin(log, ws)
    handle.checkpoint("files", files=[{"path": "a", "before": None, "after": "x"}])
    handle.set_process(4242, 99, "run-1")

    [record] = log.list(ws)
    assert record.action_id == "act-1" and record.state == "running"
    assert record.pid == os.getpid() and record.host == this_host() and record.head_before == "abc"
    assert record.checkpoints[0]["kind"] == "files" and record.process["pid"] == 4242
    assert record.owner_id == "A" and record.task_id == "t1"

    handle.finish()

    assert log.list(ws) == []


def test_journals_are_per_workspace(state, tmp_path, ws):
    other = (tmp_path / "other").resolve()
    other.mkdir()
    log = OperationsLog(state)
    begin(log, ws, "a")
    begin(log, other, "b")

    assert [r.action_id for r in log.list(ws)] == ["a"]
    assert sorted(r.action_id for r in log.list()) == ["a", "b"]


def test_the_same_action_id_twice_does_not_collide(state, ws):
    log = OperationsLog(state)
    first, second = begin(log, ws, "dup"), begin(log, ws, "dup")

    assert len(log.list(ws)) == 2
    first.finish()
    assert len(log.list(ws)) == 1
    second.finish()


def test_an_unwritable_journal_raises_journal_error(tmp_path, ws):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory")

    with pytest.raises(JournalError):
        begin(OperationsLog(blocker), ws)


def test_checkpoint_failures_are_recorded_not_raised(state, ws, monkeypatch):
    handle = begin(OperationsLog(state), ws)
    from execution.recovery import ops

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(ops, "atomic_write_bytes", boom)

    handle.checkpoint("files", files=[])

    assert any("disk full" in e for e in handle.errors)


def test_checkpoints_are_bounded(state, ws):
    handle = begin(OperationsLog(state), ws)

    for n in range(50):
        handle.checkpoint("step", n=n)
    handle.checkpoint("files", files=[{"path": str(i)} for i in range(1000)])

    assert len(handle.record.checkpoints) == 20


# ----------------------------------------------------------------- store audit


def test_audit_reports_orphans_missing_blobs_and_corruption(tmp_path):
    store = ArtifactStore(tmp_path / "s")
    owner = Owner("w" * 16)
    good = store.put("fine", kind="log", owner=owner)
    lost = store.put("will lose its blob", kind="log", owner=owner)
    forged = store.put("will be forged", kind="log", owner=owner)
    store._blob(lost.id).unlink()
    store._blob(forged.id).write_bytes(b"tampered")
    (store._objects / ("art_" + "9" * 24 + ".blob")).write_bytes(b"orphan")
    (store.spool_dir() / "leftover.log").write_bytes(b"raw")

    shallow = store.audit()
    deep = store.audit(deep=True)

    assert shallow["orphan_blobs"] == ["art_" + "9" * 24] and shallow["missing_blobs"] == [lost.id]
    assert shallow["spool_files"] == ["leftover.log"] and shallow["clean"] is False
    assert shallow["corrupt"] == [] and deep["corrupt"] == [forged.id]
    assert store.read(good.id, workspace=owner.workspace_id)[0] == b"fine"  # report only: nothing repaired
    assert (store._objects / ("art_" + "9" * 24 + ".blob")).exists()


def test_a_healthy_store_audits_clean(tmp_path):
    store = ArtifactStore(tmp_path / "s")
    store.put("x", kind="log", owner=Owner("w" * 16))

    assert store.audit(deep=True)["clean"] is True


# ----------------------------------------------------------------- git locks


@pytest.fixture
def repo(tmp_path):
    return init_repo(tmp_path / "repo")


@pytest.fixture
def fast_retries(monkeypatch):
    monkeypatch.setattr(git_repo, "LOCK_RETRY_DELAYS", (0.01, 0.01, 0.01))


@pytest.mark.asyncio
async def test_a_stuck_index_lock_is_reported_not_removed(repo, fast_retries):
    write(repo, "a.txt", "changed\n")
    lock = repo / ".git" / "index.lock"
    lock.write_text("held by someone else")
    gw = make_gateway(repo, {"fs.read", "git.read", "git.write"})

    result = await run(gw, "GIT_STAGE", {"paths": ["a.txt"]})

    assert not result.success and result.error_category == "GIT_FAILURE"
    assert result.data["lock_conflict"] is True and result.data["retries"] == 3
    assert result.data["lock_path"].endswith("index.lock")
    assert result.data["stale_candidate"] is False and result.data["lock_age_seconds"] < 60
    assert lock.exists() and lock.read_text() == "held by someone else"  # never deleted for us


@pytest.mark.asyncio
async def test_an_old_lock_is_flagged_as_a_stale_candidate(repo, fast_retries):
    write(repo, "a.txt", "changed\n")
    lock = repo / ".git" / "index.lock"
    lock.write_text("x")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    gw = make_gateway(repo, {"fs.read", "git.read", "git.write"})

    result = await run(gw, "GIT_STAGE", {"paths": ["a.txt"]})

    assert result.data["stale_candidate"] is True and result.data["lock_age_seconds"] > 3000
    assert lock.exists()


@pytest.mark.asyncio
async def test_a_brief_lock_is_waited_out(repo, monkeypatch):
    monkeypatch.setattr(git_repo, "LOCK_RETRY_DELAYS", (0.15, 0.3, 0.6))
    write(repo, "a.txt", "changed\n")
    lock = repo / ".git" / "index.lock"
    lock.write_text("brief")
    threading.Timer(0.2, lambda: lock.unlink()).start()
    gw = make_gateway(repo, {"fs.read", "git.read", "git.write"})

    result = await run(gw, "GIT_STAGE", {"paths": ["a.txt"]})

    assert result.success, result.stderr
    assert "a.txt" in git(repo, "diff", "--cached", "--name-only")
