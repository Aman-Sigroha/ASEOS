import os

import pytest

from execution.git.diff import get_diff
from execution.git.status import get_status
from execution.gateway.capabilities import CAP_FS_READ
from execution.tests import gitfixtures
from execution.tests.gitfixtures import git, init_repo, make_gateway, run, write


@pytest.fixture
def repo(tmp_path):
    return init_repo(tmp_path / "repo")


@pytest.fixture
def gateway(repo):
    return make_gateway(repo)


# ---------------------------------------------------------------- status


@pytest.mark.asyncio
async def test_status_of_clean_repo(gateway, repo):
    result = await run(gateway, "GIT_STATUS")

    assert result.success is True, result.stderr
    assert result.tool == "git.status"
    assert result.data["clean"] is True
    assert result.data["branch"] == "main"
    assert result.data["head_sha"] == git(repo, "rev-parse", "HEAD").strip()
    assert result.data["state"] is None


@pytest.mark.asyncio
async def test_status_classifies_changes(gateway, repo):
    write(repo, "a.txt", "changed\n")  # unstaged
    write(repo, "new.txt", "new\n")  # untracked
    git(repo, "mv", "b.txt", "c.txt")  # staged rename
    write(repo, "d.txt", "dee\n")
    git(repo, "add", "d.txt")  # staged add

    result = await run(gateway, "GIT_STATUS")

    data = result.data
    assert data["clean"] is False
    assert data["unstaged"] == ["a.txt"]
    assert data["untracked"] == ["new.txt"]
    assert sorted(data["staged"]) == ["c.txt", "d.txt"]
    renamed = next(e for e in data["entries"] if e["path"] == "c.txt")
    assert renamed["index"] == "renamed"
    assert renamed["original_path"] == "b.txt"
    assert data["counts"] == {
        "staged": 2,
        "unstaged": 1,
        "untracked": 1,
        "conflicted": 0,
    }


@pytest.mark.asyncio
async def test_status_reports_deleted_file(gateway, repo):
    (repo / "a.txt").unlink()

    result = await run(gateway, "GIT_STATUS")

    entry = result.data["entries"][0]
    assert (entry["path"], entry["worktree"]) == ("a.txt", "deleted")


@pytest.mark.asyncio
async def test_status_handles_spaces_and_unicode_paths(gateway, repo):
    write(repo, "dir/my file é.txt", "x\n")

    result = await run(gateway, "GIT_STATUS")

    assert result.data["untracked"] == ["dir/my file é.txt"]


@pytest.mark.asyncio
async def test_status_truncates_entries(gateway, repo):
    for i in range(5):
        write(repo, f"u{i}.txt", "x\n")

    result = await run(gateway, "GIT_STATUS", {"max_entries": 2})

    assert len(result.data["untracked"]) == 2
    assert result.data["truncated"] is True
    assert result.truncated is True
    assert result.data["counts"]["untracked"] == 5


@pytest.mark.asyncio
async def test_status_of_repo_without_commits(tmp_path):
    repo = init_repo(tmp_path / "empty", commit=False)
    write(repo, "a.txt", "x\n")

    result = await run(make_gateway(repo), "GIT_STATUS")

    assert result.success is True, result.stderr
    assert result.data["head_sha"] is None
    assert result.data["branch"] == "main"
    assert result.data["untracked"] == ["a.txt"]


@pytest.mark.asyncio
async def test_status_detects_merge_conflict(gateway, repo):
    git(repo, "checkout", "-b", "feature")
    write(repo, "a.txt", "feature\n")
    git(repo, "commit", "-am", "feature change")
    git(repo, "checkout", "main")
    write(repo, "a.txt", "main\n")
    git(repo, "commit", "-am", "main change")
    merge = __import__("subprocess").run(
        ["git", "-C", str(repo), "merge", "feature"], capture_output=True
    )
    assert merge.returncode != 0

    result = await run(gateway, "GIT_STATUS")

    assert result.data["state"] == "merge"
    assert result.data["conflicted"] == ["a.txt"]
    assert result.data["counts"]["conflicted"] == 1


@pytest.mark.asyncio
async def test_status_reports_ahead_of_upstream(tmp_path):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    repo = init_repo(tmp_path / "work")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")
    write(repo, "a.txt", "local\n")
    git(repo, "commit", "-am", "local commit")

    result = await run(make_gateway(repo), "GIT_STATUS")

    assert result.data["upstream"] == "origin/main"
    assert (result.data["ahead"], result.data["behind"]) == (1, 0)


@pytest.mark.asyncio
async def test_git_tools_fail_outside_a_repository(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    # make sure an enclosing repo cannot be picked up by accident
    gateway = make_gateway(plain)

    result = await run(gateway, "GIT_STATUS")

    assert result.success is False
    assert result.error_category == "GIT_FAILURE"


@pytest.mark.asyncio
async def test_git_tools_refuse_a_subdirectory_of_a_repository(repo):
    sub = repo / "sub"
    sub.mkdir()

    result = await run(make_gateway(sub), "GIT_STATUS")

    assert result.error_category == "GIT_FAILURE"
    assert "not the root" in result.stderr


@pytest.mark.asyncio
async def test_git_tools_need_git_read_capability(repo):
    gateway = make_gateway(repo, {CAP_FS_READ})

    result = await run(gateway, "GIT_STATUS")

    assert result.error_category == "PERMISSION_DENIED"


# ------------------------------------------------------------------ diff


@pytest.mark.asyncio
async def test_diff_unstaged_includes_untracked_as_added(gateway, repo):
    write(repo, "a.txt", "two\n")
    write(repo, "new.txt", "hello\nworld\n")

    result = await run(gateway, "GIT_DIFF")

    assert result.success is True, result.stderr
    assert "-one" in result.stdout and "+two" in result.stdout
    assert "+++ b/new.txt" in result.stdout
    files = {f["path"]: f for f in result.data["files"]}
    assert files["a.txt"]["status"] == "modified"
    assert (files["a.txt"]["additions"], files["a.txt"]["deletions"]) == (1, 1)
    assert files["new.txt"]["status"] == "added"
    assert files["new.txt"]["untracked"] is True
    assert files["new.txt"]["additions"] == 2
    assert result.data["stats"] == {"files": 2, "additions": 3, "deletions": 1}
    assert result.data["base"] == "the index (staged state)"
    assert "diff" not in result.data


@pytest.mark.asyncio
async def test_diff_can_exclude_untracked(gateway, repo):
    write(repo, "a.txt", "two\n")
    write(repo, "new.txt", "hello\n")

    result = await run(gateway, "GIT_DIFF", {"include_untracked": False})

    assert result.data["changed_files"] == ["a.txt"]
    assert "new.txt" not in result.stdout


@pytest.mark.asyncio
async def test_diff_staged_mode(gateway, repo):
    write(repo, "a.txt", "staged\n")
    git(repo, "add", "a.txt")
    write(repo, "b.txt", "unstaged\n")

    staged = await run(gateway, "GIT_DIFF", {"mode": "staged"})
    unstaged = await run(gateway, "GIT_DIFF", {"mode": "unstaged"})

    assert staged.data["changed_files"] == ["a.txt"]
    assert staged.data["base"] == "HEAD"
    assert staged.data["base_commit"] == git(repo, "rev-parse", "HEAD").strip()
    assert unstaged.data["changed_files"] == ["b.txt"]


@pytest.mark.asyncio
async def test_diff_staged_rename_has_similarity(gateway, repo):
    git(repo, "mv", "b.txt", "c.txt")

    result = await run(gateway, "GIT_DIFF", {"mode": "staged"})

    entry = result.data["files"][0]
    assert (entry["status"], entry["path"]) == ("renamed", "c.txt")
    assert entry["original_path"] == "b.txt"
    assert entry["similarity"] == 100


@pytest.mark.asyncio
async def test_diff_against_explicit_base(gateway, repo):
    base = git(repo, "rev-parse", "HEAD").strip()
    write(repo, "a.txt", "committed later\n")
    git(repo, "commit", "-am", "second")
    write(repo, "b.txt", "uncommitted\n")
    write(repo, "extra.txt", "untracked\n")

    result = await run(gateway, "GIT_DIFF", {"mode": "base", "base": base})

    assert result.success is True, result.stderr
    assert result.data["base_commit"] == base
    assert sorted(result.data["changed_files"]) == [
        "a.txt",
        "b.txt",
        "extra.txt",
    ]


@pytest.mark.asyncio
async def test_diff_base_mode_validates_the_ref(gateway):
    missing = await run(gateway, "GIT_DIFF", {"mode": "base"})
    unknown = await run(
        gateway, "GIT_DIFF", {"mode": "base", "base": "no-such-ref"}
    )
    option = await run(
        gateway, "GIT_DIFF", {"mode": "base", "base": "--output=evil.txt"}
    )

    assert missing.error_category == "INVALID_ARGUMENTS"
    assert unknown.error_category == "INVALID_ARGUMENTS"
    assert option.error_category == "INVALID_ARGUMENTS"


@pytest.mark.asyncio
async def test_diff_path_filter_and_traversal(gateway, repo):
    write(repo, "a.txt", "x\n")
    write(repo, "b.txt", "y\n")

    only_a = await run(gateway, "GIT_DIFF", {"paths": ["a.txt"]})
    escape = await run(gateway, "GIT_DIFF", {"paths": ["../outside.txt"]})
    absolute = await run(gateway, "GIT_DIFF", {"paths": ["/etc/passwd"]})

    assert only_a.data["changed_files"] == ["a.txt"]
    assert escape.error_category == "PERMISSION_DENIED"
    assert absolute.error_category == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_diff_treats_paths_literally_not_as_globs(gateway, repo):
    write(repo, "a.txt", "x\n")

    result = await run(gateway, "GIT_DIFF", {"paths": ["*.txt"]})

    assert result.data["changed_files"] == []


@pytest.mark.asyncio
async def test_diff_is_bounded(gateway, repo):
    write(repo, "a.txt", "line\n" * 500)

    result = await run(gateway, "GIT_DIFF", {"max_bytes": 100})

    assert result.truncated is True
    assert len(result.stdout.encode("utf-8")) <= 100
    assert result.data["total_bytes"] > 100


@pytest.mark.asyncio
async def test_diff_marks_binary_files(gateway, repo):
    (repo / "blob.bin").write_bytes(b"\x00\x01\x02")
    git(repo, "add", "blob.bin")
    git(repo, "commit", "-m", "binary")
    (repo / "blob.bin").write_bytes(b"\x00\x01\x02\x03")

    result = await run(gateway, "GIT_DIFF")

    entry = result.data["files"][0]
    assert entry["binary"] is True
    assert entry["additions"] is None


@pytest.mark.asyncio
async def test_diff_skips_large_untracked_files(gateway, repo, monkeypatch):
    monkeypatch.setattr(
        "execution.git.diff.MAX_UNTRACKED_FILE_BYTES", 10
    )
    write(repo, "big.txt", "x" * 50)

    result = await run(gateway, "GIT_DIFF")

    assert result.data["skipped_untracked"] == [
        {"path": "big.txt", "reason": "file too large"}
    ]
    assert result.data["changed_files"] == []


@pytest.mark.asyncio
async def test_diff_preserves_non_ascii_content(gateway, repo):
    write(repo, "a.txt", "┌─ box → ✓\n")

    result = await run(gateway, "GIT_DIFF")

    assert "┌─ box → ✓" in result.stdout


@pytest.mark.asyncio
async def test_diff_in_repo_without_commits(tmp_path):
    repo = init_repo(tmp_path / "empty", commit=False)
    write(repo, "a.txt", "x\n")
    git(repo, "add", "a.txt")

    result = await run(make_gateway(repo), "GIT_DIFF", {"mode": "staged"})

    assert result.success is True, result.stderr
    assert result.data["changed_files"] == ["a.txt"]
    assert "no commits" in result.data["base"]


# ------------------------------------------------------------------ info


@pytest.mark.asyncio
async def test_info_describes_repository(gateway, repo):
    git(repo, "branch", "other")

    result = await run(gateway, "GIT_INFO")

    data = result.data
    assert result.success is True, result.stderr
    assert data["repository_root"] == str(repo)
    assert data["branch"] == "main"
    assert data["default_branch"] == "main"
    assert data["is_linked_worktree"] is False
    assert data["unborn"] is False
    assert {b["name"] for b in data["branches"]} == {"main", "other"}
    assert next(b for b in data["branches"] if b["current"])["name"] == "main"
    assert data["recent_commits"][0]["subject"] == "initial commit"
    assert data["config"]["user_name_set"] is True
    assert data["config"]["commit_gpgsign"] == "false"


@pytest.mark.asyncio
async def test_info_for_linked_worktree(repo, tmp_path):
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-b", "task", str(linked))

    result = await run(make_gateway(linked.resolve()), "GIT_INFO")

    assert result.success is True, result.stderr
    assert result.data["is_linked_worktree"] is True
    assert result.data["branch"] == "task"


@pytest.mark.asyncio
async def test_info_limits_commits_and_branches(gateway, repo):
    for i in range(4):
        write(repo, "a.txt", f"v{i}\n")
        git(repo, "commit", "-am", f"c{i}")

    result = await run(gateway, "GIT_INFO", {"max_commits": 2})

    assert len(result.data["recent_commits"]) == 2


# ------------------------------------------------------------ hardening


def test_a_repo_cannot_run_programs_through_fsmonitor(repo):
    marker = repo / "fsmonitor_ran"
    git(repo, "config", "core.fsmonitor", "touch fsmonitor_ran; echo")

    # control: plain git runs the configured program (if this machine can)
    import subprocess

    subprocess.run(
        ["git", "-C", str(repo), "status"], capture_output=True
    )

    if not marker.exists():
        pytest.skip("fsmonitor hook did not run on this machine")

    marker.unlink()

    get_status(repo)

    assert not marker.exists()


def test_redirecting_environment_variables_are_ignored(
    repo, tmp_path, monkeypatch
):
    other = init_repo(tmp_path / "other")
    write(other, "other_only.txt", "x\n")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    write(repo, "mine.txt", "x\n")

    status = get_status(repo)

    assert status["untracked"] == ["mine.txt"]


def test_diff_function_rejects_unknown_mode(repo):
    from execution.git.repo import GitError

    with pytest.raises(GitError):
        get_diff(repo, mode="bogus")


def test_fixture_module_is_importable():
    assert gitfixtures.WRITE_CAPS
    assert os.name  # keeps the import used on every platform
