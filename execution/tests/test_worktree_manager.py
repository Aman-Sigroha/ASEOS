import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from execution.git.repo import GitError
from execution.tests.gitfixtures import git, init_repo, write
from execution.worktrees.ids import branch_for
from execution.worktrees.manager import WorktreeManager
from execution.worktrees.record import WorktreeRecord
from execution.worktrees.store import repo_lock

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def repo(tmp_path):
    return init_repo(tmp_path / "repo")


@pytest.fixture
def manager(repo):
    return WorktreeManager(repo)


def worktree_paths(repo):
    out = git(repo, "worktree", "list", "--porcelain")
    return [
        os.path.normcase(str(Path(line[9:]).resolve()))
        for line in out.splitlines()
        if line.startswith("worktree ")
    ]


def rmtree(path):
    def force(func, p, _info):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    shutil.rmtree(path, onerror=force)


def branches(repo):
    return git(repo, "branch", "--format=%(refname:short)").split()


# ---------------------------------------------------------------- create


def test_create_makes_a_registered_isolated_worktree(manager, repo):
    record = manager.create("task-1", owner="run-7")

    assert record.status == "active"
    assert record.task_id == "task-1"
    assert record.owner == "run-7"
    assert record.branch == "aseos/task-1"
    assert record.base_commit == git(repo, "rev-parse", "HEAD").strip()
    assert Path(record.path).is_dir()
    assert (Path(record.path) / "a.txt").read_text() == "one\n"
    assert git(Path(record.path), "rev-parse", "HEAD").strip() == record.base_commit
    assert git(Path(record.path), "branch", "--show-current").strip() == "aseos/task-1"
    assert os.path.normcase(record.path) in worktree_paths(repo)
    assert manager.get(record.worktree_id) == record


def test_registry_lives_in_git_dir_not_in_the_checkout(manager, repo):
    record = manager.create("task-1")

    registry = repo / ".git" / "aseos" / "worktrees" / f"{record.worktree_id}.json"
    assert json.loads(registry.read_text())["task_id"] == "task-1"
    assert sorted(p.name for p in repo.iterdir() if p.name != ".git") == [
        ".aseos_worktrees",
        "a.txt",
        "b.txt",
    ]


def test_main_checkout_is_untouched_and_does_not_see_the_worktree(manager, repo):
    write(repo, "a.txt", "user's uncommitted edit\n")
    write(repo, "notes.txt", "user's untracked file\n")
    before = git(repo, "status", "--porcelain")

    record = manager.create("task-1")

    assert git(repo, "status", "--porcelain") == before
    assert (repo / "a.txt").read_text() == "user's uncommitted edit\n"
    # the task starts from the committed state, not the user's edits
    assert (Path(record.path) / "a.txt").read_text() == "one\n"
    assert not (Path(record.path) / "notes.txt").exists()
    assert ".aseos_worktrees" not in before


def test_exclude_entry_is_added_once(manager, repo):
    manager.create("task-1")
    manager.create("task-2")

    lines = (repo / ".git" / "info" / "exclude").read_text().splitlines()

    assert lines.count("/.aseos_worktrees/") == 1


def test_one_live_worktree_per_task(manager):
    first = manager.create("task-1")

    with pytest.raises(GitError) as info:
        manager.create("task-1")

    assert info.value.data["worktree_id"] == first.worktree_id
    assert manager.find_live("task-1").worktree_id == first.worktree_id
    assert len(manager.list()) == 1


def test_task_id_can_be_reused_after_removal(manager):
    first = manager.create("task-1")
    manager.remove(first.worktree_id)

    second = manager.create("task-1")

    assert second.worktree_id != first.worktree_id
    assert manager.find_live("task-1").worktree_id == second.worktree_id


def test_existing_directory_without_a_record_is_never_reused(manager, repo):
    stray = repo / ".aseos_worktrees" / "task-1"
    stray.mkdir(parents=True)
    (stray / "precious.txt").write_text("keep me")

    with pytest.raises(GitError) as info:
        manager.create("task-1")

    assert "no worktree record" in info.value.message
    assert (stray / "precious.txt").read_text() == "keep me"
    assert manager.list() == []


def test_existing_branch_is_never_reused(manager, repo):
    git(repo, "branch", "aseos/task-1")

    with pytest.raises(GitError) as info:
        manager.create("task-1")

    assert "already exists" in info.value.message
    assert manager.list() == []
    assert not (repo / ".aseos_worktrees" / "task-1").exists()


@pytest.mark.parametrize("task_id", ["../x", "a/b", "", "-x", "a..b", "x.lock"])
def test_create_rejects_unsafe_task_ids(manager, task_id):
    with pytest.raises(ValueError):
        manager.create(task_id)

    assert manager.list() == []


def test_create_from_an_explicit_base(manager, repo):
    first = git(repo, "rev-parse", "HEAD").strip()
    write(repo, "a.txt", "two\n")
    git(repo, "commit", "-am", "second")

    record = manager.create("task-1", base=first)

    assert record.base_commit == first
    assert record.base_ref == first
    assert (Path(record.path) / "a.txt").read_text() == "one\n"


def test_create_rejects_bad_bases(manager, tmp_path):
    with pytest.raises(GitError):
        manager.create("task-1", base="no-such-ref")

    with pytest.raises(GitError):
        manager.create("task-2", base="--evil")

    empty = init_repo(tmp_path / "empty", commit=False)

    with pytest.raises(GitError):
        WorktreeManager(empty).create("task-1")


def test_worktrees_cannot_be_managed_from_another_worktree(manager):
    record = manager.create("task-1")

    with pytest.raises(GitError) as info:
        WorktreeManager(record.path)

    assert info.value.category == "PERMISSION_DENIED"


def test_manager_requires_the_repository_root(repo):
    sub = repo / "sub"
    sub.mkdir()

    with pytest.raises(GitError):
        WorktreeManager(sub)


def test_failed_create_leaves_no_record(manager, monkeypatch):
    def broken(args, **kwargs):
        if args[:2] == ["worktree", "add"]:
            raise GitError("GIT_FAILURE", "simulated failure")

        return real(args, **kwargs)

    real = manager._git
    monkeypatch.setattr(manager, "_git", broken)

    with pytest.raises(GitError):
        manager.create("task-1")

    assert manager.list() == []
    assert manager.store.list() == ([], [])


# ---------------------------------------------------------------- assess


def test_assess_clean_worktree_is_safe_to_remove(manager):
    record = manager.create("task-1")

    assessment = manager.assess(record)

    assert assessment["exists"] and assessment["registered_in_git"]
    assert assessment["dirty"] is False
    assert assessment["unreviewed"] is False
    assert assessment["safe_to_remove"] is True
    assert assessment["commits_ahead"] == 0


def test_assess_reports_uncommitted_changes(manager):
    record = manager.create("task-1")
    path = Path(record.path)
    write(path, "a.txt", "edited\n")
    write(path, "new.txt", "new\n")

    assessment = manager.assess(record)

    assert assessment["dirty"] is True
    assert assessment["changes"]["unstaged"] == ["a.txt"]
    assert assessment["changes"]["untracked"] == ["new.txt"]
    assert assessment["cleanup_blockers"] == ["uncommitted changes"]


def test_assess_counts_unmerged_commits_then_sees_them_merged(manager, repo):
    record = manager.create("task-1")
    path = Path(record.path)
    write(path, "a.txt", "agent\n")
    git(path, "commit", "-am", "agent work")

    unmerged = manager.assess(record)
    git(repo, "merge", "--ff-only", record.branch)
    merged = manager.assess(record)

    assert unmerged["commits_ahead"] == 1
    assert unmerged["unreviewed"] is True
    assert "1 commit(s)" in unmerged["cleanup_blockers"][0]
    assert merged["commits_elsewhere"] is True
    assert merged["unreviewed"] is False


def test_mark_records_task_outcome(manager):
    record = manager.create("task-1")

    done = manager.mark(record.worktree_id, "completed", "tests passed")

    assert done.status == "completed"
    assert manager.get(record.worktree_id).note == "tests passed"
    assert manager.find_live("task-1") is not None

    with pytest.raises(GitError):
        manager.mark(record.worktree_id, "removed")

    with pytest.raises(GitError):
        manager.mark("wt-unknown", "failed")


# ---------------------------------------------------------------- remove


def test_remove_clean_worktree(manager, repo):
    record = manager.create("task-1")

    result = manager.remove(record.worktree_id)

    assert result["removed"] is True
    assert result["discarded_unreviewed"] is False
    assert result["branch_deleted"] is True
    assert not Path(record.path).exists()
    assert "aseos/task-1" not in branches(repo)
    assert os.path.normcase(record.path) not in worktree_paths(repo)
    assert manager.get(record.worktree_id).status == "removed"
    assert manager.list() == []
    assert manager.remove(record.worktree_id)["already"] is True


def test_remove_refuses_uncommitted_changes(manager, repo):
    record = manager.create("task-1")
    path = Path(record.path)
    write(path, "a.txt", "valuable work\n")
    write(path, "new.txt", "also valuable\n")

    with pytest.raises(GitError) as info:
        manager.remove(record.worktree_id)

    assert info.value.category == "PERMISSION_DENIED"
    assert info.value.data["blockers"] == ["uncommitted changes"]
    assert (path / "a.txt").read_text() == "valuable work\n"
    assert (path / "new.txt").exists()
    assert "aseos/task-1" in branches(repo)
    assert manager.get(record.worktree_id).status == "active"


def test_confirmed_removal_backs_up_uncommitted_work(manager, repo):
    record = manager.create("task-1")
    path = Path(record.path)
    write(path, "a.txt", "valuable work\n")
    write(path, "new.txt", "brand new file\n")

    result = manager.remove(record.worktree_id, discard_confirmed=True)

    assert result["discarded_unreviewed"] is True
    backup = Path(result["backup"]["files"]["patch"])
    text = backup.read_text(encoding="utf-8")
    assert "+valuable work" in text and "+brand new file" in text
    assert result["backup"]["complete"] is True
    assert not path.exists()
    assert manager.get(record.worktree_id).backups["patch"] == str(backup)

    # the backup really restores the work onto a clean checkout
    git(repo, "apply", "--check", str(backup))


def test_backup_reports_what_it_could_not_capture(manager):
    record = manager.create("task-1")
    path = Path(record.path)
    (path / "blob.bin").write_bytes(b"\x00\x01\x02")

    result = manager.remove(record.worktree_id, discard_confirmed=True)

    assert result["backup"]["complete"] is False
    assert "blob.bin" in result["backup"]["notes"][0]


def test_remove_refuses_unmerged_commits_and_keeps_them_recoverable(
    manager, repo
):
    record = manager.create("task-1")
    path = Path(record.path)
    write(path, "a.txt", "committed agent work\n")
    git(path, "commit", "-am", "agent work")
    tip = git(path, "rev-parse", "HEAD").strip()

    with pytest.raises(GitError) as info:
        manager.remove(record.worktree_id)

    assert info.value.data["commits_ahead"] == 1
    assert path.exists() and "aseos/task-1" in branches(repo)

    result = manager.remove(record.worktree_id, discard_confirmed=True)

    ref = result["backup"]["files"]["ref"]
    assert git(repo, "rev-parse", ref).strip() == tip
    assert git(repo, "cat-file", "-t", tip).strip() == "commit"
    assert result["branch_deleted"] is True
    assert "aseos/task-1" not in branches(repo)


def test_remove_allows_merged_commits_without_confirmation(manager, repo):
    record = manager.create("task-1")
    path = Path(record.path)
    write(path, "a.txt", "agent\n")
    git(path, "commit", "-am", "agent work")
    git(repo, "merge", "--ff-only", record.branch)

    result = manager.remove(record.worktree_id)

    assert result["removed"] is True
    assert (repo / "a.txt").read_text() == "agent\n"


def test_remove_refuses_locked_worktrees(manager, repo):
    record = manager.create("task-1")
    git(repo, "worktree", "lock", record.path)

    with pytest.raises(GitError) as info:
        manager.remove(record.worktree_id, discard_confirmed=True)

    assert "locked" in info.value.message
    assert Path(record.path).exists()


def test_remove_refuses_a_tampered_record_path(manager, repo, tmp_path):
    record = manager.create("task-1")
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "important.txt").write_text("do not delete")
    registry = (
        repo / ".git" / "aseos" / "worktrees" / f"{record.worktree_id}.json"
    )
    data = json.loads(registry.read_text())
    data["path"] = str(victim)
    registry.write_text(json.dumps(data))

    with pytest.raises(GitError) as info:
        manager.remove(record.worktree_id, discard_confirmed=True)

    assert "managed layout" in info.value.message
    assert (victim / "important.txt").read_text() == "do not delete"
    assert Path(record.path).exists()


def test_remove_refuses_a_directory_git_does_not_know(manager, repo):
    path = repo / ".aseos_worktrees" / "task-1"
    path.mkdir(parents=True)
    (path / "plain.txt").write_text("not a worktree")
    record = WorktreeRecord(
        worktree_id="wt-manual",
        task_id="task-1",
        repo_id=manager.repo_id,
        repo_root=str(repo),
        path=str(path.resolve()),
        branch="aseos/task-1",
        base_ref="HEAD",
        base_commit=git(repo, "rev-parse", "HEAD").strip(),
        status="active",
    )
    manager.store.save(record)

    with pytest.raises(GitError) as info:
        manager.remove("wt-manual", discard_confirmed=True)

    assert "not a Git worktree" in info.value.message
    assert (path / "plain.txt").read_text() == "not a worktree"


def test_failed_git_removal_restores_the_previous_status(manager, monkeypatch):
    record = manager.create("task-1")
    manager.mark(record.worktree_id, "completed")
    real = manager._git

    def broken(args, **kwargs):
        if args[:2] == ["worktree", "remove"]:
            raise GitError("GIT_FAILURE", "file in use")

        return real(args, **kwargs)

    monkeypatch.setattr(manager, "_git", broken)

    with pytest.raises(GitError):
        manager.remove(record.worktree_id)

    assert manager.get(record.worktree_id).status == "completed"
    assert Path(record.path).exists()


def test_remove_unknown_id(manager):
    with pytest.raises(GitError):
        manager.remove("wt-nope")


def test_removal_never_touches_the_main_checkout(manager, repo):
    write(repo, "a.txt", "user edit\n")
    record = manager.create("task-1")
    write(Path(record.path), "a.txt", "agent edit\n")

    manager.remove(record.worktree_id, discard_confirmed=True)

    assert (repo / "a.txt").read_text() == "user edit\n"
    assert git(repo, "status", "--porcelain").strip() == "M a.txt"


# ------------------------------------------------------------- reconcile


def test_reconcile_of_a_healthy_registry(manager):
    record = manager.create("task-1")

    report = WorktreeManager(manager.repo).reconcile()  # "after a restart"

    assert report["ok"] == [record.worktree_id]
    assert report["missing"] == report["orphan_dirs"] == []


def test_reconcile_detects_a_vanished_directory_and_keeps_the_branch(
    manager, repo
):
    record = manager.create("task-1")
    path = Path(record.path)
    write(path, "a.txt", "committed\n")
    git(path, "commit", "-am", "work")
    rmtree(path)

    report = manager.reconcile()

    assert report["missing"] == [record.worktree_id]
    assert report["prune_candidates"] == [record.worktree_id]
    assert manager.get(record.worktree_id).status == "missing"
    assert "aseos/task-1" in branches(repo)

    pruned = manager.prune_missing(record.worktree_id)

    assert pruned["branch_kept"] is True
    assert pruned["commits_ahead"] == 1
    assert "aseos/task-1" in branches(repo)
    assert os.path.normcase(record.path) not in worktree_paths(repo)
    assert manager.get(record.worktree_id).status == "removed"


def test_prune_missing_refuses_when_the_directory_exists(manager):
    record = manager.create("task-1")

    with pytest.raises(GitError):
        manager.prune_missing(record.worktree_id)


def test_remove_of_a_missing_directory_prunes_git_entry(manager, repo):
    record = manager.create("task-1")
    rmtree(Path(record.path))

    result = manager.remove(record.worktree_id)

    assert result["removed"] is True
    assert os.path.normcase(record.path) not in worktree_paths(repo)
    assert "aseos/task-1" not in branches(repo)


def test_reconcile_reports_but_never_deletes_orphan_directories(manager, repo):
    orphan = repo / ".aseos_worktrees" / "mystery"
    orphan.mkdir(parents=True)
    (orphan / "data.txt").write_text("unknown provenance")

    report = manager.reconcile()

    assert report["orphan_dirs"] == [str(orphan.resolve())]
    assert (orphan / "data.txt").read_text() == "unknown provenance"


def test_reconcile_reports_unrecorded_git_worktrees(manager, repo):
    git(
        repo,
        "worktree",
        "add",
        "-b",
        "manual",
        str(repo / ".aseos_worktrees" / "manual"),
    )

    report = manager.reconcile()

    assert len(report["unrecorded_git_worktrees"]) == 1
    assert report["unrecorded_git_worktrees"][0].endswith("manual")
    assert (repo / ".aseos_worktrees" / "manual").is_dir()


def test_reconcile_recovers_an_interrupted_create(manager):
    record = manager.create("task-1")
    manager.store.save(record.model_copy(update={"status": "creating"}))

    report = manager.reconcile()

    assert report["recovered"] == [record.worktree_id]
    assert manager.get(record.worktree_id).status == "active"


def test_reconcile_drops_a_create_that_never_reached_git(manager, repo):
    record = WorktreeRecord(
        worktree_id="wt-crashed",
        task_id="task-9",
        repo_id=manager.repo_id,
        repo_root=str(repo),
        path=str((repo / ".aseos_worktrees" / "task-9").resolve()),
        branch="aseos/task-9",
        base_ref="HEAD",
        base_commit=git(repo, "rev-parse", "HEAD").strip(),
        status="creating",
    )
    manager.store.save(record)

    report = manager.reconcile()

    assert report["recovered"] == ["wt-crashed"]
    assert manager.store.load("wt-crashed") is None


def test_reconcile_finishes_a_removal_that_completed_but_was_not_recorded(
    manager, repo
):
    record = manager.create("task-1")
    manager._set(record, "removing")
    git(repo, "worktree", "remove", record.path)

    report = manager.reconcile()

    assert report["recovered"] == [record.worktree_id]
    assert manager.get(record.worktree_id).status == "removed"


def test_reconcile_flags_an_interrupted_removal_without_deleting(manager):
    record = manager.create("task-1")
    write(Path(record.path), "a.txt", "work\n")
    manager._set(record, "removing")

    report = manager.reconcile()

    assert report["interrupted"] == [record.worktree_id]
    assert (Path(record.path) / "a.txt").read_text() == "work\n"


def test_reconcile_tolerates_a_corrupt_record(manager, repo):
    good = manager.create("task-1")
    (repo / ".git" / "aseos" / "worktrees" / "wt-bad.json").write_text("{nope")

    report = manager.reconcile()

    assert report["corrupt_records"] == ["wt-bad.json"]
    assert report["ok"] == [good.worktree_id]


# ----------------------------------------------------------- concurrency


def test_concurrent_creates_for_different_tasks_all_succeed(manager, repo):
    results, errors = [], []

    def work(index):
        try:
            results.append(WorktreeManager(repo).create(f"task-{index}"))
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert errors == []
    assert len({r.worktree_id for r in results}) == 6
    assert len({r.path for r in results}) == 6
    assert len(worktree_paths(repo)) == 7  # main + 6
    assert len(manager.list()) == 6


def test_concurrent_creates_for_the_same_task_allow_exactly_one(repo):
    wins, losses = [], []

    def work():
        try:
            wins.append(WorktreeManager(repo).create("same-task"))
        except GitError as exc:
            losses.append(exc)

    threads = [threading.Thread(target=work) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert len(wins) == 1
    assert len(losses) == 4
    assert len(WorktreeManager(repo).list()) == 1


def test_creates_from_two_processes_do_not_collide(repo):
    code = (
        "import sys\n"
        "from execution.worktrees.manager import WorktreeManager\n"
        "WorktreeManager(sys.argv[1]).create(sys.argv[2])\n"
    )
    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(repo), f"proc-{i}"],
            cwd=PROJECT_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for i in range(3)
    ]
    outputs = [p.communicate(timeout=120) for p in procs]

    assert [p.returncode for p in procs] == [0, 0, 0], outputs
    assert len(WorktreeManager(repo).list()) == 3


def test_a_held_lock_blocks_then_times_out(tmp_path):
    lock = tmp_path / "lock"
    release = threading.Event()
    held = threading.Event()

    def holder():
        with repo_lock(lock):
            held.set()
            release.wait(5)

    thread = threading.Thread(target=holder)
    thread.start()
    held.wait(5)

    # another process cannot be simulated with the same-thread RLock, so
    # check the file lock directly
    from execution.worktrees.store import _acquire_file

    with pytest.raises(GitError) as info:
        _acquire_file(lock, timeout=0.2, stale_after=300)

    assert info.value.data["lock_conflict"] is True
    release.set()
    thread.join()
    assert not lock.exists()


def test_a_stale_lock_from_a_dead_process_is_broken(tmp_path):
    lock = tmp_path / "lock"
    lock.write_text("99999 0")
    old = time.time() - 1000
    os.utime(lock, (old, old))

    with repo_lock(lock, timeout=2, stale_after=300):
        assert lock.exists()

    assert not lock.exists()


def test_lock_is_reentrant_within_a_thread(tmp_path):
    lock = tmp_path / "lock"

    with repo_lock(lock):
        with repo_lock(lock):
            assert lock.exists()

        assert lock.exists()

    assert not lock.exists()


def test_each_task_gets_its_own_directory_and_branch(manager):
    a = manager.create("task-a")
    b = manager.create("task-b")
    write(Path(a.path), "a.txt", "from a\n")

    assert a.path != b.path
    assert branch_for("task-a") != branch_for("task-b")
    assert (Path(b.path) / "a.txt").read_text() == "one\n"
