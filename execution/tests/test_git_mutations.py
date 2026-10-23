import hashlib
import subprocess

import pytest

from execution.tests.gitfixtures import (
    ALL_GIT_CAPS,
    WRITE_CAPS,
    git,
    init_repo,
    make_gateway,
    run,
    write,
)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def repo(tmp_path):
    return init_repo(tmp_path / "repo")


@pytest.fixture
def gateway(repo):
    return make_gateway(repo, ALL_GIT_CAPS)


def porcelain(repo):
    return git(repo, "status", "--porcelain").splitlines()


# ---------------------------------------------------------- capabilities


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action_type, payload",
    [
        ("GIT_STAGE", {"paths": ["a.txt"]}),
        ("GIT_COMMIT", {"message": "m", "paths": ["a.txt"]}),
        ("GIT_BRANCH", {"name": "x"}),
        ("GIT_DISCARD", {"paths": ["a.txt"]}),
    ],
)
async def test_mutations_are_denied_by_default(repo, action_type, payload):
    write(repo, "a.txt", "changed\n")
    before = porcelain(repo)

    result = await run(make_gateway(repo), action_type, payload)

    assert result.error_category == "PERMISSION_DENIED"
    assert porcelain(repo) == before


@pytest.mark.asyncio
async def test_discard_needs_its_own_capability(repo):
    write(repo, "a.txt", "changed\n")

    result = await run(
        make_gateway(repo, WRITE_CAPS), "GIT_DISCARD", {"paths": ["a.txt"]}
    )

    assert result.error_category == "PERMISSION_DENIED"
    assert (repo / "a.txt").read_text() == "changed\n"


# ----------------------------------------------------------------- stage


@pytest.mark.asyncio
async def test_stage_stages_only_named_files_and_reports_others(
    gateway, repo
):
    write(repo, "a.txt", "agent change\n")
    write(repo, "b.txt", "user's own edit\n")
    write(repo, "notes.txt", "user's untracked file\n")

    result = await run(gateway, "GIT_STAGE", {"paths": ["a.txt"]})

    assert result.success is True, result.stderr
    assert result.tool == "git.stage"
    assert result.data["staged_now"] == ["a.txt"]
    assert result.data["other_changes"] == ["b.txt", "notes.txt"]
    assert git(repo, "diff", "--cached", "--name-only").split() == ["a.txt"]


@pytest.mark.asyncio
async def test_stage_a_new_file_and_a_deletion(gateway, repo):
    write(repo, "new.txt", "n\n")
    (repo / "b.txt").unlink()

    result = await run(gateway, "GIT_STAGE", {"paths": ["new.txt", "b.txt"]})

    assert result.success is True, result.stderr
    assert sorted(result.data["staged_now"]) == ["b.txt", "new.txt"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "paths, category",
    [
        ([], "INVALID_ARGUMENTS"),
        (["."], "INVALID_ARGUMENTS"),
        (["sub/"], "INVALID_ARGUMENTS"),
        (["../outside.txt"], "PERMISSION_DENIED"),
        (["/abs/path"], "PERMISSION_DENIED"),
        (["a.txt", "a.txt"], "INVALID_ARGUMENTS"),
        (["missing.txt"], "INVALID_ARGUMENTS"),
        ([".env"], "PERMISSION_DENIED"),
        (["*.txt"], "INVALID_ARGUMENTS"),
    ],
)
async def test_stage_rejects_unsafe_scopes(gateway, repo, paths, category):
    write(repo, "a.txt", "changed\n")
    write(repo, ".env", "SECRET=1\n")

    result = await run(gateway, "GIT_STAGE", {"paths": paths})

    assert result.success is False
    assert result.error_category == category
    assert git(repo, "diff", "--cached", "--name-only") == ""


@pytest.mark.asyncio
async def test_stage_rejects_a_directory(gateway, repo):
    write(repo, "sub/file.txt", "x\n")

    result = await run(gateway, "GIT_STAGE", {"paths": ["sub"]})

    assert result.error_category == "INVALID_ARGUMENTS"
    assert git(repo, "diff", "--cached", "--name-only") == ""


@pytest.mark.asyncio
async def test_stage_checks_expected_hash(gateway, repo):
    write(repo, "a.txt", "agent version\n")

    stale = await run(
        gateway,
        "GIT_STAGE",
        {"paths": ["a.txt"], "expected_sha256": {"a.txt": sha("other\n")}},
    )
    ok = await run(
        gateway,
        "GIT_STAGE",
        {
            "paths": ["a.txt"],
            "expected_sha256": {"a.txt": sha("agent version\n")},
        },
    )

    assert stale.error_category == "PATCH_CONFLICT"
    assert stale.data["actual_sha256"] == sha("agent version\n")
    assert ok.success is True, ok.stderr


@pytest.mark.asyncio
async def test_stage_expected_hash_for_deleted_file(gateway, repo):
    (repo / "a.txt").unlink()

    result = await run(
        gateway,
        "GIT_STAGE",
        {"paths": ["a.txt"], "expected_sha256": {"a.txt": None}},
    )

    assert result.success is True, result.stderr


@pytest.mark.asyncio
async def test_stage_hash_for_unlisted_path_is_rejected(gateway, repo):
    write(repo, "a.txt", "x\n")

    result = await run(
        gateway,
        "GIT_STAGE",
        {"paths": ["a.txt"], "expected_sha256": {"b.txt": sha("x\n")}},
    )

    assert result.error_category == "INVALID_ARGUMENTS"


@pytest.mark.asyncio
async def test_stage_reports_index_lock_conflicts(gateway, repo):
    write(repo, "a.txt", "changed\n")
    (repo / ".git" / "index.lock").write_text("", encoding="utf-8")

    result = await run(gateway, "GIT_STAGE", {"paths": ["a.txt"]})

    assert result.error_category == "GIT_FAILURE"
    assert result.data["lock_conflict"] is True


@pytest.mark.asyncio
async def test_stage_refuses_during_a_merge(gateway, repo):
    git(repo, "checkout", "-b", "feature")
    write(repo, "a.txt", "feature\n")
    git(repo, "commit", "-am", "f")
    git(repo, "checkout", "main")
    write(repo, "a.txt", "main\n")
    git(repo, "commit", "-am", "m")
    subprocess.run(["git", "-C", str(repo), "merge", "feature"], capture_output=True)

    result = await run(gateway, "GIT_STAGE", {"paths": ["b.txt"]})

    assert result.error_category == "GIT_FAILURE"
    assert result.data["state"] == "merge"


# ---------------------------------------------------------------- commit


@pytest.mark.asyncio
async def test_commit_commits_exactly_the_named_files(gateway, repo):
    write(repo, "a.txt", "agent change\n")
    write(repo, "b.txt", "user's unstaged edit\n")
    await run(gateway, "GIT_STAGE", {"paths": ["a.txt"]})

    result = await run(
        gateway,
        "GIT_COMMIT",
        {"message": "Fix a\n\nLonger body.", "paths": ["a.txt"]},
    )

    assert result.success is True, result.stderr
    assert result.data["commit"] == git(repo, "rev-parse", "HEAD").strip()
    assert result.data["subject"] == "Fix a"
    assert result.data["branch"] == "main"
    assert git(repo, "log", "-1", "--format=%B").startswith("Fix a")
    assert git(repo, "show", "--name-only", "--format=", "HEAD").split() == [
        "a.txt"
    ]
    # the user's unrelated edit is still there, untouched and uncommitted
    assert (repo / "b.txt").read_text() == "user's unstaged edit\n"
    assert porcelain(repo) == [" M b.txt"]
    assert result.data["other_changes"] == ["b.txt"]


@pytest.mark.asyncio
async def test_commit_refuses_changes_the_user_staged_themselves(
    gateway, repo
):
    write(repo, "a.txt", "agent change\n")
    write(repo, "b.txt", "user staged this\n")
    git(repo, "add", "a.txt", "b.txt")
    head_before = git(repo, "rev-parse", "HEAD")

    result = await run(
        gateway, "GIT_COMMIT", {"message": "agent", "paths": ["a.txt"]}
    )

    assert result.error_category == "GIT_FAILURE"
    assert result.data["unexpected_staged"] == ["b.txt"]
    assert git(repo, "rev-parse", "HEAD") == head_before
    assert sorted(git(repo, "diff", "--cached", "--name-only").split()) == [
        "a.txt",
        "b.txt",
    ]


@pytest.mark.asyncio
async def test_commit_refuses_when_named_file_is_not_staged(gateway, repo):
    write(repo, "a.txt", "changed\n")

    result = await run(
        gateway, "GIT_COMMIT", {"message": "x", "paths": ["a.txt"]}
    )

    assert result.error_category == "GIT_FAILURE"
    assert result.data["missing_staged"] == ["a.txt"]


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["", "   \n  "])
async def test_commit_rejects_empty_message(gateway, repo, message):
    write(repo, "a.txt", "changed\n")
    git(repo, "add", "a.txt")

    result = await run(
        gateway, "GIT_COMMIT", {"message": message, "paths": ["a.txt"]}
    )

    assert result.error_category == "INVALID_ARGUMENTS"


@pytest.mark.asyncio
async def test_commit_rejects_overlong_message(gateway, repo):
    write(repo, "a.txt", "changed\n")
    git(repo, "add", "a.txt")

    result = await run(
        gateway, "GIT_COMMIT", {"message": "x" * 5000, "paths": ["a.txt"]}
    )

    assert result.error_category == "RESOURCE_LIMIT"


@pytest.mark.asyncio
async def test_commit_message_is_not_interpreted_by_a_shell(gateway, repo):
    write(repo, "a.txt", "changed\n")
    git(repo, "add", "a.txt")
    message = '"; echo hacked > pwned.txt; "$(whoami)` --amend'

    result = await run(
        gateway, "GIT_COMMIT", {"message": message, "paths": ["a.txt"]}
    )

    assert result.success is True, result.stderr
    assert git(repo, "log", "-1", "--format=%s").strip() == message
    assert not (repo / "pwned.txt").exists()


@pytest.mark.asyncio
async def test_commit_refuses_detached_head(gateway, repo):
    git(repo, "checkout", "--detach")
    write(repo, "a.txt", "changed\n")
    git(repo, "add", "a.txt")

    result = await run(
        gateway, "GIT_COMMIT", {"message": "x", "paths": ["a.txt"]}
    )

    assert result.error_category == "GIT_FAILURE"
    assert "detached" in result.stderr


@pytest.mark.asyncio
async def test_commit_in_repo_without_commits(tmp_path):
    repo = init_repo(tmp_path / "empty", commit=False)
    gateway = make_gateway(repo, ALL_GIT_CAPS)
    write(repo, "a.txt", "first\n")
    await run(gateway, "GIT_STAGE", {"paths": ["a.txt"]})

    result = await run(
        gateway, "GIT_COMMIT", {"message": "first", "paths": ["a.txt"]}
    )

    assert result.success is True, result.stderr
    assert git(repo, "rev-list", "--count", "HEAD").strip() == "1"


@pytest.mark.asyncio
async def test_commit_of_a_staged_rename_needs_both_paths(gateway, repo):
    git(repo, "mv", "b.txt", "c.txt")

    only_new = await run(
        gateway, "GIT_COMMIT", {"message": "mv", "paths": ["c.txt"]}
    )
    both = await run(
        gateway, "GIT_COMMIT", {"message": "mv", "paths": ["b.txt", "c.txt"]}
    )

    assert only_new.error_category == "GIT_FAILURE"
    assert both.success is True, both.stderr


# ---------------------------------------------------------------- branch


@pytest.mark.asyncio
async def test_branch_create_does_not_check_it_out(gateway, repo):
    write(repo, "b.txt", "user's edit\n")

    result = await run(gateway, "GIT_BRANCH", {"name": "aseos/fix-1"})

    assert result.success is True, result.stderr
    assert result.data["checked_out"] is False
    assert git(repo, "branch", "--show-current").strip() == "main"
    assert "aseos/fix-1" in git(repo, "branch", "--list")
    assert (repo / "b.txt").read_text() == "user's edit\n"


@pytest.mark.asyncio
async def test_branch_create_from_start_point(gateway, repo):
    first = git(repo, "rev-parse", "HEAD").strip()
    write(repo, "a.txt", "two\n")
    git(repo, "commit", "-am", "second")

    result = await run(
        gateway, "GIT_BRANCH", {"name": "old", "start_point": first}
    )

    assert result.data["commit"] == first
    assert git(repo, "rev-parse", "old").strip() == first


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name", ["", "-x", "bad name", "a..b", "x.lock", "~tilde", "a b"]
)
async def test_branch_create_rejects_invalid_names(gateway, repo, name):
    result = await run(gateway, "GIT_BRANCH", {"name": name})

    assert result.success is False
    assert result.error_category == "INVALID_ARGUMENTS"
    assert git(repo, "branch", "--list").split() == ["*", "main"]


@pytest.mark.asyncio
async def test_branch_create_never_moves_an_existing_branch(gateway, repo):
    result = await run(gateway, "GIT_BRANCH", {"name": "main"})

    assert result.error_category == "GIT_FAILURE"
    assert "already exists" in result.stderr


@pytest.mark.asyncio
async def test_branch_create_rejects_bad_start_point(gateway):
    result = await run(
        gateway, "GIT_BRANCH", {"name": "x", "start_point": "--evil"}
    )

    assert result.error_category == "INVALID_ARGUMENTS"


@pytest.mark.asyncio
async def test_branch_create_in_repo_without_commits(tmp_path):
    repo = init_repo(tmp_path / "empty", commit=False)

    result = await run(
        make_gateway(repo, ALL_GIT_CAPS), "GIT_BRANCH", {"name": "x"}
    )

    assert result.error_category == "GIT_FAILURE"


# --------------------------------------------------------------- discard


@pytest.mark.asyncio
async def test_discard_restores_file_and_returns_recoverable_diff(
    gateway, repo
):
    write(repo, "a.txt", "agent edit\n")
    write(repo, "b.txt", "user's edit\n")

    result = await run(gateway, "GIT_DISCARD", {"paths": ["a.txt"]})

    assert result.success is True, result.stderr
    assert (repo / "a.txt").read_bytes() == b"one\n"
    assert (repo / "b.txt").read_text() == "user's edit\n"
    assert "+agent edit" in result.data["discarded_diff"]
    assert result.data["other_changes"] == ["b.txt"]

    # the returned diff is enough to get the discarded work back
    restored = await run(
        gateway, "APPLY_PATCH", {"patch": result.data["discarded_diff"]}
    )
    assert restored.success is True, restored.stderr
    assert (repo / "a.txt").read_bytes() == b"agent edit\n"


@pytest.mark.asyncio
async def test_discard_refuses_untracked_unchanged_and_staged(gateway, repo):
    write(repo, "untracked.txt", "u\n")
    write(repo, "a.txt", "staged edit\n")
    git(repo, "add", "a.txt")
    write(repo, "a.txt", "staged edit\nplus unstaged\n")

    untracked = await run(gateway, "GIT_DISCARD", {"paths": ["untracked.txt"]})
    unchanged = await run(gateway, "GIT_DISCARD", {"paths": ["b.txt"]})
    staged = await run(gateway, "GIT_DISCARD", {"paths": ["a.txt"]})

    assert untracked.error_category == "INVALID_ARGUMENTS"
    assert unchanged.error_category == "INVALID_ARGUMENTS"
    assert staged.error_category == "GIT_FAILURE"
    assert (repo / "untracked.txt").exists()
    assert (repo / "a.txt").read_text() == "staged edit\nplus unstaged\n"


@pytest.mark.asyncio
async def test_discard_restores_a_deleted_file(gateway, repo):
    (repo / "a.txt").unlink()

    result = await run(gateway, "GIT_DISCARD", {"paths": ["a.txt"]})

    assert result.success is True, result.stderr
    assert (repo / "a.txt").read_bytes() == b"one\n"


@pytest.mark.asyncio
async def test_discard_refuses_when_diff_cannot_be_returned_in_full(
    gateway, repo, monkeypatch
):
    monkeypatch.setattr("execution.git.mutate.MAX_DISCARD_DIFF_BYTES", 20)
    write(repo, "a.txt", "line\n" * 50)

    result = await run(gateway, "GIT_DISCARD", {"paths": ["a.txt"]})

    assert result.error_category == "RESOURCE_LIMIT"
    assert (repo / "a.txt").read_text() == "line\n" * 50


@pytest.mark.asyncio
async def test_discard_rejects_globs_directories_and_escapes(gateway, repo):
    write(repo, "a.txt", "changed\n")

    results = [
        await run(gateway, "GIT_DISCARD", {"paths": paths})
        for paths in (["*"], ["."], ["../x"])
    ]

    assert [r.success for r in results] == [False, False, False]
    assert (repo / "a.txt").read_text() == "changed\n"


# ------------------------------------------------------------ end to end


@pytest.mark.asyncio
async def test_patch_stage_commit_flow_in_a_linked_worktree(repo, tmp_path):
    """The defining flow: change in an isolated worktree, main untouched."""

    linked = tmp_path / "task-worktree"
    git(repo, "worktree", "add", "-b", "aseos/task-1", str(linked))
    linked = linked.resolve()
    gateway = make_gateway(linked, ALL_GIT_CAPS)
    patch = "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-one\n+uno\n"

    applied = await run(gateway, "APPLY_PATCH", {"patch": patch})
    after = applied.data["files"][0]["sha256_after"]
    staged = await run(
        gateway,
        "GIT_STAGE",
        {"paths": ["a.txt"], "expected_sha256": {"a.txt": after}},
    )
    committed = await run(
        gateway,
        "GIT_COMMIT",
        {"message": "a: one -> uno", "paths": ["a.txt"]},
    )
    diff = await run(
        gateway,
        "GIT_DIFF",
        {"mode": "base", "base": git(repo, "rev-parse", "main").strip()},
    )

    assert applied.success and staged.success and committed.success
    assert committed.data["branch"] == "aseos/task-1"
    assert diff.data["changed_files"] == ["a.txt"]
    assert "+uno" in diff.stdout
    # the main checkout is untouched
    assert (repo / "a.txt").read_text() == "one\n"
    assert git(repo, "rev-parse", "main").strip() != git(
        repo, "rev-parse", "aseos/task-1"
    ).strip()
    assert porcelain(repo) == []
