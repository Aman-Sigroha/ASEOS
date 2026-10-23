"""Verification checks: parsing, globs, each check kind against a real repo."""

import pytest
from pydantic import ValidationError

from execution.gateway import ExecutionGateway
from execution.gateway.capabilities import CAP_SHELL_RUN
from execution.gateway.tools import DEFAULT_CAPABILITIES, default_tools
from execution.tests.gitfixtures import git, init_repo, write
from execution.verification import (
    CHECK_KINDS,
    CommandCheck,
    DiffContainsCheck,
    FileContentCheck,
    FileExistsCheck,
    NoProhibitedPathsCheck,
    TestsCheck,
    fold_status,
    parse_checks,
    report_is_current,
    run_checks,
)
from execution.verification.globs import GlobError, GlobSet, compile_glob
from execution.verification.service import parse_diff_lines

SHELL_CAPS = DEFAULT_CAPABILITIES | {CAP_SHELL_RUN}


@pytest.fixture
def repo(tmp_path):
    return init_repo(tmp_path / "repo")


def gateway(repo, caps=DEFAULT_CAPABILITIES):
    return ExecutionGateway(str(repo), tools=default_tools(), granted_capabilities=caps)


async def verify(repo, *checks, caps=DEFAULT_CAPABILITIES, **kw):
    return await run_checks(gateway(repo, caps), list(checks), **kw)


# ------------------------------------------------------------------ globs


@pytest.mark.parametrize(
    "pattern, path, ignore_case, expected",
    [
        (".env", ".env", False, True),
        (".env", "svc/api/.env", False, True),  # bare name: any depth
        (".env", "svc/api/.env.local", False, False),
        ("*.pem", "a/b/key.pem", False, True),
        ("src/*.py", "src/a.py", False, True),
        ("src/*.py", "src/sub/a.py", False, False),  # * stays in one segment
        ("src/**/*.py", "src/sub/deep/a.py", False, True),
        ("src/**/*.py", "src/a.py", False, True),
        ("docs/", "docs/a/b.md", False, True),  # trailing slash = directory
        ("docs/", "docsx/a.md", False, False),
        ("migrations/**", "migrations/001.sql", False, True),
        ("src/a.py", "other/src/a.py", False, False),  # with a slash: anchored
        ("SECRETS/", "secrets/k.txt", True, True),
        ("SECRETS/", "secrets/k.txt", False, False),
        ("./src/a.py", "src/a.py", False, True),
        ("a?c.txt", "abc.txt", False, True),
        ("a?c.txt", "a/c.txt", False, False),
    ],
)
def test_glob_matching(pattern, path, ignore_case, expected):
    assert (GlobSet([pattern], ignore_case=ignore_case).match(path) is not None) is expected


def test_glob_regex_metacharacters_are_literal():
    assert GlobSet(["a(b).txt"], ignore_case=False).match("a(b).txt")
    assert not GlobSet(["a.txt"], ignore_case=False).match("abtxt")
    assert not GlobSet(["a+.txt"], ignore_case=False).match("aaa.txt")


@pytest.mark.parametrize(
    "pattern", ["", "   ", "/etc/passwd", "../x", "a/../b", "**/**/**/**/x", "a" * 201]
)
def test_unsafe_or_degenerate_globs_are_rejected(pattern):
    with pytest.raises(GlobError):
        compile_glob(pattern, ignore_case=False)


def test_glob_with_many_stars_cannot_backtrack_catastrophically():
    regex = compile_glob("**/**/**/x", ignore_case=False)

    assert regex.match("a/" * 2000 + "y") is None  # returns promptly


# --------------------------------------------------------- parsing checks


def test_checks_parse_from_plain_dicts_into_their_kinds():
    checks = parse_checks(
        [
            {"kind": "tests", "targets": ["tests/test_a.py"]},
            {"kind": "build", "command": "python -m compileall ."},
            {"kind": "lint", "command": ["python", "-m", "ruff", "check"]},
            {"kind": "typecheck", "command": "python -m mypy src"},
            {"kind": "file_exists", "path": "README.md"},
            {"kind": "file_content", "path": "a.txt", "contains": ["x"]},
            {"kind": "diff_contains", "added_contains": ["x"]},
            {"kind": "no_prohibited_paths", "prohibited": [".github/"]},
        ]
    )

    assert [c.kind for c in checks] == list(CHECK_KINDS)
    assert isinstance(checks[0], TestsCheck) and isinstance(checks[1], CommandCheck)


@pytest.mark.parametrize(
    "raw",
    [
        {"kind": "teleport"},
        {"kind": "tests", "surprise": 1},  # extra fields are refused
        {"kind": "tests", "timeout_seconds": 0},
        {"kind": "tests", "require_full_run": True, "targets": ["a"]},
        {"kind": "build", "command": ""},
        {"kind": "build", "command": []},
        {"kind": "build", "command": ["a", ""]},
        {"kind": "file_exists", "path": ""},
        {"kind": "file_content", "path": "a"},  # no condition
        {"kind": "file_content", "path": "a", "contains": [""]},
        {"kind": "file_content", "path": "a", "sha256": "abc"},
        {"kind": "diff_contains"},  # no condition
        {"kind": "diff_contains", "added_contains": ["x" * 2001]},
        {"kind": "no_prohibited_paths"},
        {"kind": "no_prohibited_paths", "prohibited": ["../x"]},
        {"kind": "no_prohibited_paths", "allowed_only": ["/abs"]},
    ],
)
def test_invalid_checks_are_rejected_before_anything_runs(raw):
    with pytest.raises(ValidationError):
        parse_checks([raw])


def test_fold_status_precedence_and_empty_is_not_a_pass():
    assert fold_status(["PASS", "PASS"]) == "PASS"
    assert fold_status(["PASS", "FAIL"]) == "FAIL"
    assert fold_status(["FAIL", "ERROR"]) == "ERROR"
    assert fold_status(["ERROR", "TIMEOUT", "FAIL"]) == "TIMEOUT"
    assert fold_status([]) == "ERROR"


@pytest.mark.asyncio
async def test_no_checks_is_an_error_not_a_pass(repo):
    report = await verify(repo)

    assert report.status == "ERROR" and "No verification checks" in report.summary


# ---------------------------------------------------------------- file_exists


@pytest.mark.asyncio
async def test_file_exists_checks(repo):
    (repo / "pkg").mkdir()
    write(repo, "pkg/mod.py", "x = 1\n")

    report = await verify(
        repo,
        FileExistsCheck(path="a.txt"),
        FileExistsCheck(path="pkg", type="dir"),
        FileExistsCheck(path="pkg", type="file"),  # wrong type
        FileExistsCheck(path="nope.txt"),
        FileExistsCheck(path="nope.txt", must_exist=False),
        FileExistsCheck(path="a.txt", must_exist=False),  # exists but must not
    )

    assert [c.status for c in report.checks] == ["PASS", "PASS", "FAIL", "FAIL", "PASS", "FAIL"]
    assert "is a dir, expected file" in report.checks[2].message
    assert report.status == "FAIL"


@pytest.mark.asyncio
async def test_a_path_outside_the_workspace_cannot_be_checked(repo):
    report = await verify(repo, FileExistsCheck(path="../outside.txt"))

    check = report.checks[0]
    assert check.status == "ERROR"
    assert check.traces[0].error_category == "PERMISSION_DENIED"
    assert check.next_steps == ["replan", "stop"]  # retrying would not help


# --------------------------------------------------------------- file_content


@pytest.mark.asyncio
async def test_file_content_conditions(repo):
    write(repo, "src/app.py", "def main():\n    return 42\n")

    report = await verify(
        repo,
        FileContentCheck(path="src/app.py", contains=["def main():", "return 42"]),
        FileContentCheck(path="src/app.py", contains=["return 43"]),
        FileContentCheck(path="src/app.py", not_contains=["TODO"]),
        FileContentCheck(path="src/app.py", not_contains=["return 42"]),
        FileContentCheck(path="missing.py", contains=["x"]),
        FileContentCheck(path="src", contains=["x"]),
    )

    statuses = [c.status for c in report.checks]
    assert statuses == ["PASS", "FAIL", "PASS", "FAIL", "FAIL", "FAIL"]
    assert report.checks[1].observed["missing"] == ["return 43"]
    assert report.checks[3].observed["forbidden_present"] == ["return 42"]
    assert "does not exist" in report.checks[4].message
    assert "not a regular file" in report.checks[5].message


@pytest.mark.asyncio
async def test_file_content_sha256(repo):
    write(repo, "data.txt", "hello\n")
    import hashlib

    good = hashlib.sha256(b"hello\n").hexdigest()

    report = await verify(
        repo,
        FileContentCheck(path="data.txt", sha256=good),
        FileContentCheck(path="data.txt", sha256=good.upper()),
        FileContentCheck(path="data.txt", sha256="0" * 64),
        FileContentCheck(path="data.txt", sha256=good, contains=["hello"]),
    )

    assert [c.status for c in report.checks] == ["PASS", "PASS", "FAIL", "PASS"]
    assert report.checks[2].observed["expected_sha256"] == "0" * 64


@pytest.mark.asyncio
async def test_crlf_files_match_lf_conditions(repo):
    (repo / "win.txt").write_bytes(b"one\r\ntwo\r\n")

    report = await verify(repo, FileContentCheck(path="win.txt", contains=["one\ntwo"]))

    assert report.checks[0].status == "PASS"


@pytest.mark.asyncio
async def test_secret_files_are_never_read_for_verification(repo):
    write(repo, ".env", "API_KEY=supersecretvalue\n")

    report = await verify(repo, FileContentCheck(path=".env", contains=["supersecretvalue"]))

    check = report.checks[0]
    assert check.status == "ERROR" and "restricted" in check.message
    assert "supersecretvalue" not in check.model_dump_json().replace('"contains"', "")
    assert all(t.tool != "repo.read" for t in check.traces)


@pytest.mark.asyncio
async def test_binary_and_oversized_files_are_errors_not_guesses(repo):
    (repo / "blob.bin").write_bytes(bytes(range(256)) * 10)
    write(repo, "big.txt", "x" * (3 * 1024 * 1024))

    report = await verify(
        repo,
        FileContentCheck(path="blob.bin", contains=["x"]),
        FileContentCheck(path="big.txt", contains=["x"]),
    )

    assert [c.status for c in report.checks] == ["ERROR", "ERROR"]


# ------------------------------------------------------------- diff_contains


def test_parse_diff_lines_uses_position_not_prefix_to_skip_headers():
    diff = (
        "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n"
        " keep\n-old line\n+new line\n+++ looks like a header but is content\n"
        "diff --git a/g b/g\nnew file mode 100644\n--- /dev/null\n+++ b/g\n@@ -0,0 +1 @@\n+g line\n"
    )

    added, removed = parse_diff_lines(diff)

    assert added == ["new line", "++ looks like a header but is content", "g line"]
    assert removed == ["old line"]


@pytest.mark.asyncio
async def test_diff_contains_sees_edits_and_new_files(repo):
    write(repo, "a.txt", "one\nTWO changed\n")
    write(repo, "new_module.py", "def feature():\n    return 'shipped'\n")

    report = await verify(
        repo,
        DiffContainsCheck(added_contains=["TWO changed", "def feature():"]),
        DiffContainsCheck(removed_contains=["one"], min_files_changed=2),
        DiffContainsCheck(added_contains=["never written"]),
        DiffContainsCheck(min_files_changed=5),
    )

    assert [c.status for c in report.checks] == ["PASS", "FAIL", "FAIL", "FAIL"]
    # "one" is still present (unchanged line), so it was not removed.
    assert report.checks[1].observed["missing_removed"] == ["one"]
    assert report.checks[0].observed["files_changed"] == 2
    assert "new_module.py" in report.checks[0].observed["changed_files"]


@pytest.mark.asyncio
async def test_diff_against_an_unknown_base_is_an_error(repo):
    report = await verify(repo, DiffContainsCheck(base="no-such-ref", added_contains=["x"]))

    assert report.checks[0].status == "ERROR"
    assert report.checks[0].traces[0].error_category == "INVALID_ARGUMENTS"


@pytest.mark.asyncio
async def test_missing_lines_in_an_incomplete_diff_are_not_called_failures(repo):
    write(repo, ".env", "TOKEN=abc\n")  # untracked secret: content not shown

    report = await verify(repo, DiffContainsCheck(added_contains=["TOKEN=abc"]))

    check = report.checks[0]
    assert check.status == "ERROR" and "incomplete" in check.message
    assert check.observed["skipped_untracked"]


@pytest.mark.asyncio
async def test_committed_work_is_covered_by_a_branch_base(repo):
    git(repo, "checkout", "-b", "feature")
    write(repo, "feat.py", "FLAG = True\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "feature")

    report = await verify(
        repo,
        DiffContainsCheck(base="main", added_contains=["FLAG = True"]),
        DiffContainsCheck(base="HEAD", added_contains=["FLAG = True"]),
    )

    assert [c.status for c in report.checks] == ["PASS", "FAIL"]


# ---------------------------------------------------------- no_prohibited_paths


@pytest.mark.asyncio
async def test_allowed_changes_pass_and_prohibited_ones_fail(repo):
    write(repo, "a.txt", "edited\n")
    ok = await verify(repo, NoProhibitedPathsCheck(prohibited=[".github/", "*.pem", ".env*"]))

    assert ok.status == "PASS" and ok.checks[0].observed["changed_paths"] == 1

    write(repo, ".github/workflows/ci.yml", "on: push\n")
    write(repo, "deep/dir/server.PEM", "key\n")  # case-insensitive match
    bad = await verify(repo, NoProhibitedPathsCheck(prohibited=[".github/", "*.pem"]))

    check = bad.checks[0]
    assert check.status == "FAIL"
    hit = {v["path"]: v["pattern"] for v in check.observed["violations"]}
    assert hit == {".github/workflows/ci.yml": ".github/", "deep/dir/server.PEM": "*.pem"}
    assert "inspect" in check.next_steps and "modify_patch" in check.next_steps


@pytest.mark.asyncio
async def test_staged_deleted_and_renamed_paths_are_seen(repo):
    git(repo, "mv", "a.txt", "renamed.txt")
    (repo / "b.txt").unlink()

    report = await verify(repo, NoProhibitedPathsCheck(prohibited=["a.txt", "b.txt"]))

    paths = {v["path"] for v in report.checks[0].observed["violations"]}
    assert paths == {"a.txt", "b.txt"}  # the rename source and the deletion


@pytest.mark.asyncio
async def test_allowed_only_flags_everything_outside_the_set(repo):
    write(repo, "src/x.py", "x\n")
    write(repo, "elsewhere/y.py", "y\n")

    report = await verify(repo, NoProhibitedPathsCheck(allowed_only=["src/**"]))

    check = report.checks[0]
    assert check.status == "FAIL"
    assert check.observed["outside_allowed"] == ["elsewhere/y.py"]


@pytest.mark.asyncio
async def test_allowed_only_matching_is_case_sensitive(repo):
    write(repo, "SRC/x.py", "x\n")

    report = await verify(repo, NoProhibitedPathsCheck(allowed_only=["src/**"]))

    assert report.checks[0].status == "FAIL"


@pytest.mark.asyncio
async def test_base_adds_committed_changes_to_the_checked_set(repo):
    git(repo, "checkout", "-b", "feature")
    write(repo, "infra/deploy.yml", "x\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "touch infra")
    check = dict(prohibited=["infra/"])

    without_base = await verify(repo, NoProhibitedPathsCheck(**check))
    with_base = await verify(repo, NoProhibitedPathsCheck(base="main", **check))

    assert without_base.checks[0].status == "PASS"  # clean tree: nothing uncommitted
    assert with_base.checks[0].status == "FAIL"


@pytest.mark.asyncio
async def test_a_non_repository_workspace_makes_git_checks_errors(tmp_path):
    plain = (tmp_path / "plain").resolve()
    plain.mkdir()

    report = await verify(plain, NoProhibitedPathsCheck(prohibited=["x"]))

    assert report.checks[0].status == "ERROR" and report.status == "ERROR"


# ---------------------------------------------------------------- tests check

PASSING = "def test_a(): assert True\ndef test_b(): assert 1 + 1 == 2\n"
FAILING = "def test_a(): assert True\ndef test_bad(): assert 1 == 2, 'nope'\n"


@pytest.mark.asyncio
async def test_passing_tests_are_evidence_with_counts_and_a_version(repo):
    write(repo, "test_ok.py", PASSING)

    report = await verify(repo, TestsCheck())

    check = report.checks[0]
    assert report.status == "PASS" and check.status == "PASS"
    assert check.observed["counts"]["passed"] == 2
    assert check.observed["scope"] == "full"
    assert check.observed["workspace_version"]["fingerprint"]
    assert check.traces[0].tool == "test.run" and check.traces[0].exit_code == 0
    assert check.next_steps == []


@pytest.mark.asyncio
async def test_failing_tests_say_which_failed_and_what_to_try_next(repo):
    write(repo, "test_bad.py", FAILING)

    report = await verify(repo, TestsCheck())

    check = report.checks[0]
    assert report.status == "FAIL"
    assert [c["name"] for c in check.observed["failing_cases"]] == ["test_bad"]
    assert "nope" in check.observed["failing_cases"][0]["message"]
    assert check.next_steps[:2] == ["inspect", "retest_targeted"]
    assert "modify_patch" in check.next_steps and "replan" in check.next_steps


@pytest.mark.asyncio
async def test_no_tests_and_collection_errors_do_not_pass(repo):
    write(repo, "notes.txt", "no tests here\n")
    empty = await verify(repo, TestsCheck(framework="pytest"))
    write(repo, "test_broken.py", "import does_not_exist_zzz\ndef test_a(): pass\n")
    broken = await verify(repo, TestsCheck(framework="pytest"))

    assert empty.status != "PASS" and broken.status == "FAIL"
    assert broken.checks[0].observed["test_status"] in ("ERROR", "FAILED")


@pytest.mark.asyncio
async def test_a_targeted_run_is_labelled_targeted(repo):
    write(repo, "test_a.py", PASSING)
    write(repo, "test_b.py", FAILING)

    report = await verify(repo, TestsCheck(targets=["test_a.py"]))

    assert report.status == "PASS"
    assert report.checks[0].observed["scope"] == "targeted"


@pytest.mark.asyncio
async def test_tests_without_the_capability_are_blocked_not_failed(repo):
    write(repo, "test_ok.py", PASSING)

    report = await verify(repo, TestsCheck(), caps=frozenset({"fs.read", "git.read"}))

    check = report.checks[0]
    assert check.status == "ERROR" and check.traces[0].error_category == "PERMISSION_DENIED"
    assert check.next_steps == ["replan", "stop"]


# ---------------------------------------------------- build / lint / typecheck


@pytest.mark.asyncio
async def test_build_check_passes_and_fails_on_the_real_exit_code(repo):
    write(repo, "good.py", "x = 1\n")
    good = await verify(
        repo, CommandCheck(kind="build", command="python -m compileall -q good.py"), caps=SHELL_CAPS
    )
    write(repo, "bad.py", "def broken(:\n")
    bad = await verify(
        repo, CommandCheck(kind="build", command="python -m compileall -q bad.py"), caps=SHELL_CAPS
    )

    assert good.status == "PASS" and good.checks[0].observed["exit_code"] == 0
    assert bad.status == "FAIL"
    assert bad.checks[0].observed["exit_code"] == 1
    assert "bad.py" in bad.checks[0].message or "SyntaxError" in bad.checks[0].message
    assert "modify_patch" in bad.checks[0].next_steps


@pytest.mark.asyncio
async def test_a_command_of_the_wrong_category_does_not_count(repo):
    write(repo, "test_ok.py", PASSING)

    report = await verify(
        repo, CommandCheck(kind="lint", command="python -m pytest -q"), caps=SHELL_CAPS
    )

    check = report.checks[0]
    assert check.status == "ERROR"
    assert check.observed["policy_category"] == "test"
    assert "not a 'lint' check" in check.message


@pytest.mark.asyncio
async def test_commands_need_shell_run_and_pass_the_policy(repo):
    no_cap = await verify(repo, CommandCheck(kind="build", command="python -m compileall ."))
    denied = await verify(
        repo, CommandCheck(kind="build", command="curl http://example.com"), caps=SHELL_CAPS
    )

    for report in (no_cap, denied):
        assert report.checks[0].status == "ERROR"
        assert report.checks[0].traces[0].error_category == "PERMISSION_DENIED"
        assert report.checks[0].next_steps == ["replan", "stop"]


@pytest.mark.asyncio
async def test_a_missing_tool_is_an_error_not_a_code_failure(repo):
    report = await verify(
        repo,
        CommandCheck(kind="lint", command="python -m ruff_not_a_real_tool_zz check"),
        caps=SHELL_CAPS,
    )

    # Either the policy refuses the unknown module or the module is not installed;
    # neither says anything bad about the code.
    assert report.checks[0].status == "ERROR"


@pytest.mark.asyncio
async def test_a_command_timeout_is_a_timeout(repo):
    write(repo, "slow.py", "import time\ntime.sleep(30)\n")
    # compileall finishes instantly; use a tiny timeout against pytest collecting a sleeper.
    write(repo, "test_slow.py", "import time\ntime.sleep(30)\ndef test_a(): pass\n")

    report = await verify(repo, TestsCheck(timeout_seconds=1))

    assert report.checks[0].status == "TIMEOUT"
    assert report.status == "TIMEOUT"


# ------------------------------------------------------- run-level behaviour


@pytest.mark.asyncio
async def test_overall_status_folds_checks_and_summarises(repo):
    write(repo, "a.txt", "edited\n")

    report = await verify(
        repo,
        FileExistsCheck(path="a.txt"),
        FileExistsCheck(path="missing.txt"),
        FileContentCheck(path="a.txt", contains=["edited"]),
    )

    assert report.status == "FAIL"
    assert report.summary == "2 of 3 check(s) passed, 1 fail."
    assert [c.index for c in report.checks] == [0, 1, 2]
    assert report.next_steps == ["inspect", "modify_patch", "replan"]


@pytest.mark.asyncio
async def test_fail_fast_skips_the_rest_and_names_them(repo):
    report = await verify(
        repo,
        FileExistsCheck(path="missing.txt"),
        FileExistsCheck(path="a.txt"),
        FileExistsCheck(path="b.txt"),
        fail_fast=True,
    )

    assert len(report.checks) == 1 and len(report.skipped) == 2
    assert report.status == "FAIL" and "2 skipped" in report.summary


@pytest.mark.asyncio
async def test_every_check_is_traceable_to_gateway_calls(repo):
    report = await verify(repo, FileExistsCheck(path="a.txt"), FileContentCheck(path="a.txt", contains=["one"]))

    ids = [t.action_id for c in report.checks for t in c.traces]
    assert len(ids) == len(set(ids)) == 3  # file_info; file_info + read
    assert all(i.startswith(f"verify-{report.run_id}-") for i in ids)


@pytest.mark.asyncio
async def test_a_workspace_that_changes_during_verification_is_not_a_pass(repo):
    write(repo, "test_mutating.py", "from pathlib import Path\n"
          "def test_a():\n    Path('side_effect.txt').write_text('changed the tree')\n")

    report = await verify(repo, TestsCheck(framework="pytest"))

    assert report.checks[0].status == "PASS"
    assert report.checks[0].modified_workspace is True
    assert report.stable is False
    assert report.status == "ERROR" and "cannot be tied to one revision" in report.summary

    lenient = await verify(repo, TestsCheck(framework="pytest"), require_stable=False)
    # side_effect.txt already exists from the first run, so the tree is stable now.
    assert lenient.status == "PASS"


@pytest.mark.asyncio
async def test_a_stable_workspace_reports_matching_versions(repo):
    report = await verify(repo, FileExistsCheck(path="a.txt"))

    assert report.stable is True
    assert report.version_before["fingerprint"] == report.version_after["fingerprint"]


@pytest.mark.asyncio
async def test_report_is_current_until_the_workspace_changes(repo):
    gw = gateway(repo)
    report = await run_checks(gw, [FileExistsCheck(path="a.txt")])

    assert (await report_is_current(repo, report))["current"] is True

    write(repo, "a.txt", "edited after verification\n")
    stale = await report_is_current(repo, report)

    assert stale["current"] is False and "differs" in stale["reason"]


@pytest.mark.asyncio
async def test_a_buggy_check_is_an_error_never_a_pass(repo, monkeypatch):
    from execution.verification import service

    async def boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(service, "_check_file_exists", boom)

    report = await verify(repo, FileExistsCheck(path="a.txt"))

    assert report.status == "ERROR" and "kaboom" in report.checks[0].message
