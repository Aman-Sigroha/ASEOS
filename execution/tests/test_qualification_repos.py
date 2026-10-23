"""Representative repositories, not just the happy path: a user's dirty tree,
patches that no longer apply, a project that does not build, several
languages in one repository, and awkward file names and encodings.

Where a real toolchain is needed (Node) the test is skipped when it is not
installed; no other language toolchain was available when this was written,
so the Maven/Gradle/Cargo/Go adapters remain unproven against real tools."""

import asyncio
import hashlib
import shutil
from pathlib import Path

import pytest

from execution.gateway import ExecutionGateway
from execution.gateway.capabilities import CAP_GIT_WRITE, CAP_WORKTREE_MANAGE, DEFAULT_CAPABILITIES
from execution.gateway.tools import default_tools
from execution.tests.gitfixtures import git
from execution.tests import qualification_repos as repos
from execution.verification import TestsCheck, run_checks
from execution.worktrees.manager import WorktreeManager
from runtime.schemas.action import Action

CAPS = DEFAULT_CAPABILITIES | {CAP_GIT_WRITE, CAP_WORKTREE_MANAGE}
needs_node = pytest.mark.skipif(shutil.which("node") is None or shutil.which("npm") is None, reason="Node.js is not installed")


def gateway(root, caps=CAPS, **kw):
    return ExecutionGateway(str(root), tools=default_tools(), granted_capabilities=caps, artifact_store=False, **kw)


def act(gw, action_type, payload=None, action_id="q-1"):
    return asyncio.run(gw.execute(Action(id=action_id, type=action_type, payload=payload or {})))


def snapshot(root: Path) -> dict[str, str]:
    """Every file in the tree (including untracked and ignored ones, excluding
    .git and managed worktrees) with its content hash, plus Git's own view."""

    files = {}

    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()

        if path.is_file() and not relative.startswith((".git/", ".aseos_worktrees/")):
            files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()

    porcelain = git(root, "status", "--porcelain=v1", "--untracked-files=all", "--ignored")
    # The managed worktree folder is expected to appear (as ignored) once a task exists.
    files["<status>"] = "".join(
        line for line in porcelain.splitlines(keepends=True) if ".aseos_worktrees" not in line
    )
    files["<index>"] = git(root, "diff", "--cached", "--no-color")
    files["<head>"] = git(root, "rev-parse", "HEAD")
    return files


# ---------------------------------------------------------------- dirty tree


def test_a_task_in_a_worktree_never_touches_the_users_unfinished_work(tmp_path):
    repo = repos.dirty_tree(tmp_path / "repo")
    before = snapshot(repo)
    manager = WorktreeManager(repo)
    record = manager.create("fix-mul")
    task = gateway(record.path)
    worktree = Path(record.path)

    # The task starts from the last commit: none of the user's unfinished work is there.
    assert not (worktree / "scratch.txt").exists()
    assert not (worktree / "notes.txt").exists()
    assert not (worktree / "debug.log").exists()
    assert "def sub" not in (worktree / "calc.py").read_text()

    assert act(task, "EDIT", {"file": "calc.py", "old": "return a * b", "new": "return b * a"}).success
    tests = act(task, "RUN_TEST")
    assert tests.success, tests.stderr
    diff = act(task, "GIT_DIFF", {"mode": "base", "base": record.base_commit})
    assert diff.success and "b * a" in diff.stdout
    assert act(task, "GIT_STAGE", {"paths": ["calc.py"]}).success
    assert act(task, "GIT_COMMIT", {"message": "swap operands", "paths": ["calc.py"]}).success

    # Main checkout: same files, same index, same HEAD, same ignored/untracked state.
    assert snapshot(repo) == before
    task.release_lease()


def test_a_commit_refuses_to_sweep_up_the_users_staged_changes(tmp_path):
    repo = repos.dirty_tree(tmp_path / "repo")  # notes.txt is staged by the user
    before = snapshot(repo)
    gw = gateway(repo)

    refused = act(gw, "GIT_COMMIT", {"message": "mine", "paths": ["calc.py"]})

    assert not refused.success
    assert refused.error_category == "GIT_FAILURE"
    assert snapshot(repo) == before  # nothing committed, nothing unstaged


def test_discard_is_limited_to_the_named_paths(tmp_path):
    repo = repos.dirty_tree(tmp_path / "repo")
    gw = gateway(repo, caps=CAPS | {"git.discard"})
    scratch = (repo / "scratch.txt").read_bytes()

    done = act(gw, "GIT_DISCARD", {"paths": ["calc.py"]})

    assert done.success, done.stderr
    assert "def sub" not in (repo / "calc.py").read_text()
    assert (repo / "scratch.txt").read_bytes() == scratch  # untracked work untouched
    assert (repo / "debug.log").exists()
    assert "notes.txt" in git(repo, "diff", "--cached", "--name-only")


# --------------------------------------------------------- conflicting patch

STALE = "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return b + a\n"


def test_a_patch_made_against_an_older_file_is_a_conflict_that_changes_nothing(tmp_path):
    repo = repos.python_project(tmp_path / "repo")
    before = snapshot(repo)
    result = act(gateway(repo), "APPLY_PATCH", {"patch": STALE})

    assert result.error_category == "PATCH_CONFLICT"
    assert result.data["disposition"]["action"] == "REPLAN"
    assert snapshot(repo) == before


def test_a_multi_file_patch_with_one_conflict_rolls_everything_back(tmp_path):
    repo = repos.python_project(tmp_path / "repo")
    before = snapshot(repo)
    patch = (
        "--- a/test_calc.py\n+++ b/test_calc.py\n@@ -1 +1,2 @@\n+# header\n from calc import add, mul\n"
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return b + a\n"
    )
    result = act(gateway(repo), "APPLY_PATCH", {"patch": patch})

    assert not result.success
    assert snapshot(repo) == before  # the first file was not left half-edited


def test_a_patch_keeps_crlf_and_missing_final_newline(tmp_path):
    repo = repos.awkward_files(tmp_path / "repo")
    gw = gateway(repo)
    crlf = "--- a/crlf.txt\n+++ b/crlf.txt\n@@ -1,3 +1,3 @@\n one\n-two\n+TWO\n three\n"
    result = act(gw, "APPLY_PATCH", {"patch": crlf})

    assert result.success, result.stderr
    assert (repo / "crlf.txt").read_bytes() == b"one\r\nTWO\r\nthree\r\n"

    tail = (
        "--- a/no_newline.txt\n+++ b/no_newline.txt\n@@ -1 +1 @@\n-last line without newline\n"
        "\\ No newline at end of file\n+changed last line\n\\ No newline at end of file\n"
    )
    assert act(gw, "APPLY_PATCH", {"patch": tail}, "q-2").success
    assert (repo / "no_newline.txt").read_bytes() == b"changed last line"


# ------------------------------------------------------------- failing build


def test_a_project_that_does_not_build_is_never_reported_as_passing(tmp_path):
    repo = repos.failing_build(tmp_path / "repo")
    gw = gateway(repo)
    result = act(gw, "RUN_TEST")

    assert not result.success
    assert result.data["status"] in {"ERROR", "FAILED"}
    assert result.data["status"] != "PASSED"

    report = asyncio.run(run_checks(gw, [TestsCheck()]))
    assert report.status in {"ERROR", "FAIL"}
    assert report.checks[0].next_steps  # tells the runtime what to do next


def test_failing_tests_name_the_failing_case(tmp_path):
    repo = repos.failing_tests(tmp_path / "repo")
    gw = gateway(repo)
    result = act(gw, "RUN_TEST")

    assert result.error_category == "TEST_FAILURE"
    failed = [c for c in result.data["cases"] if c["status"] == "failed"]
    assert [c["name"] for c in failed] == ["test_mul"]
    assert result.data["counts"]["passed"] == 1

    report = asyncio.run(run_checks(gw, [TestsCheck()]))
    assert report.status == "FAIL"
    assert report.checks[0].observed["failing_cases"][0]["name"] == "test_mul"


# ------------------------------------------------- a real, non-Python toolchain


@needs_node
def test_node_tests_are_run_and_counted_with_the_real_toolchain(tmp_path):
    repo = repos.node_project(tmp_path / "repo", passing=True)
    result = act(gateway(repo), "RUN_TEST")

    assert result.success, result.stderr
    assert result.data["framework"] == "npm"
    assert result.data["counts"]["passed"] == 1
    assert result.data["counts"]["skipped"] == 1


@needs_node
def test_a_failing_node_test_is_a_test_failure_with_its_case(tmp_path):
    repo = repos.node_project(tmp_path / "repo", passing=False)
    result = act(gateway(repo), "RUN_TEST")

    assert result.error_category == "TEST_FAILURE"
    assert result.data["counts"]["failed"] == 1
    assert [c["name"] for c in result.data["cases"] if c["status"] == "failed"] == ["adds"]


# ----------------------------------------------------------- several languages


@needs_node
def test_a_pass_in_one_language_says_which_suites_it_did_not_run(tmp_path):
    repo = repos.polyglot(tmp_path / "repo")
    gw = gateway(repo)

    python_side = act(gw, "RUN_TEST")  # auto picks the first detected framework
    assert python_side.success
    assert python_side.data["framework"] == "pytest"
    assert {"npm", "go"} <= set(python_side.data["other_frameworks"])
    assert "not run" in python_side.data["summary"]

    report = asyncio.run(run_checks(gw, [TestsCheck()]))
    assert report.status == "PASS"  # what it ran did pass ...
    assert {"npm", "go"} <= set(report.checks[0].observed["other_frameworks"])  # ... and it says what it skipped

    # The Node side is broken and only an explicit run shows it.
    node_side = act(gw, "RUN_TEST", {"framework": "npm"}, "q-2")
    assert node_side.error_category == "TEST_FAILURE"


# --------------------------------------------------- awkward names and content


def test_unusual_file_names_and_content_survive_status_diff_and_read(tmp_path):
    repo = repos.awkward_files(tmp_path / "repo")
    gw = gateway(repo)
    names = ["dir with space/file name.txt", "ünïcode-файл.txt", "crlf.txt", "blob.bin"]

    for name in names:
        path = repo / name
        path.write_bytes(path.read_bytes() + (b"\x00\x01" if name == "blob.bin" else b"more\n"))

    status = act(gw, "GIT_STATUS")
    assert status.success, status.stderr
    listed = {e["path"] for e in status.data["entries"]}
    assert set(names) <= listed

    diff = act(gw, "GIT_DIFF", {"max_bytes": 5000}, "q-2")
    assert diff.success, diff.stderr
    assert "file name.txt" in diff.stdout
    assert "Binary" in diff.stdout or "binary" in diff.stdout.lower()

    unicode_read = act(gw, "READ", {"path": "ünïcode-файл.txt"}, "q-3")
    assert unicode_read.success and "unicode name" in unicode_read.stdout
    assert act(gw, "READ", {"path": "blob.bin"}, "q-4").success is False  # binary is refused, not dumped

    wide = act(gw, "READ", {"path": "wide.txt"}, "q-5")
    assert wide.success and len(wide.stdout) == 200_001 and not wide.truncated  # under the 1 MB cap: returned whole

    (repo / "huge.txt").write_text("y" * 2_500_000 + "\n", encoding="utf-8")
    huge = act(gw, "READ", {"path": "huge.txt"}, "q-6")
    assert huge.truncated or not huge.success  # over the cap: cut or refused, never dumped whole
    assert len(huge.stdout) <= 1_000_000
