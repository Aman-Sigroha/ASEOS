import asyncio
import threading
from pathlib import Path

import pytest

from execution.commands.policy import CommandPolicy
from execution.commands.process import ProcessResult
from execution.gateway import ExecutionGateway
from execution.gateway.tools import DEFAULT_CAPABILITIES, default_tools
from execution.testing.adapters import ADAPTERS, TargetError
from execution.testing.model import Counts, RunReport, to_runtime_result
from execution.testing.service import _decide_status, run_test_suite
from execution.tests.proc_helpers import wait_for_file
from runtime.schemas.action import Action


@pytest.fixture
def ws(tmp_path):
    root = (tmp_path / "proj").resolve()
    root.mkdir()
    return root


def put(ws, name, text):
    path = ws / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


MIXED = (
    "import pytest\n"
    "def test_ok(): assert True\n"
    "def test_also_ok(): assert 1 + 1 == 2\n"
    "def test_bad(): assert 1 + 1 == 3, 'math is hard'\n"
    "@pytest.mark.skip(reason='later')\n"
    "def test_skipped(): pass\n"
)


def run(ws, **kw):
    kw.setdefault("timeout", 60)
    return run_test_suite(ws, **kw)


# ------------------------------------------------------------- pytest


def test_failing_tests_are_reported_with_counts_and_cases(ws):
    put(ws, "test_mixed.py", MIXED)

    report, process = run(ws)

    assert report.status == "FAILED"
    assert report.framework == "pytest" and report.scope == "full"
    c = report.counts
    assert (c.total, c.passed, c.failed, c.errors, c.skipped) == (4, 2, 1, 0, 1)
    assert report.report_source == "junit" and report.report_problem is None
    assert report.exit_code == 1 and process.exit_code == 1
    first = report.cases[0]
    assert (first.name, first.status) == ("test_bad", "failed")
    assert "math is hard" in first.message
    assert {x.status for x in report.cases} == {"failed", "skipped", "passed"}
    assert report.summary.startswith("FAILED: 2 passed, 1 failed, 1 skipped (pytest, full suite)")
    assert "pytest.ini" not in str(report.evidence) and report.evidence


def test_all_passing_is_passed(ws):
    put(ws, "test_ok.py", "def test_a(): pass\ndef test_b(): pass\n")

    report, _ = run(ws)

    assert report.status == "PASSED" and report.passed
    assert (report.counts.total, report.counts.passed) == (2, 2)
    assert report.exit_code == 0


def test_report_file_is_removed_and_the_workspace_left_clean(ws):
    put(ws, "test_ok.py", "def test_a(): pass\n")

    report, _ = run(ws)

    assert list(ws.glob(".aseos_report_*")) == []
    assert report.report_files and report.report_files[0].startswith(".aseos_report_")
    assert not report.modified_during_run


def test_a_collection_error_is_an_error_not_a_failed_test(ws):
    put(ws, "test_ok.py", "def test_a(): pass\n")
    put(ws, "test_broken.py", "import module_that_does_not_exist_xyz\ndef test_b(): pass\n")

    report, _ = run(ws)

    assert report.status == "ERROR"
    assert report.counts.errors >= 1 and report.counts.failed == 0
    assert any(c.status == "error" for c in report.cases)


def test_syntax_error_in_a_test_file(ws):
    put(ws, "test_syntax.py", "def test_a(:\n    pass\n")

    report, _ = run(ws)

    assert report.status == "ERROR"


def test_no_tests_collected(ws):
    put(ws, "pytest.ini", "[pytest]\n")
    put(ws, "test_empty.py", "x = 1\n")

    report, _ = run(ws)

    assert report.status == "NO_TESTS"
    assert report.exit_code == 5
    assert not report.passed


def test_a_suite_where_everything_is_skipped_verifies_nothing(ws):
    put(ws, "test_skip.py", "import pytest\n@pytest.mark.skip\ndef test_a(): pass\n@pytest.mark.skip\ndef test_b(): pass\n")

    report, _ = run(ws)

    assert report.status == "NO_TESTS"
    assert "skipped" in report.summary and "nothing was verified" in report.summary
    assert report.counts.skipped == 2


# -------------------------------------------------- targeted vs. full


def test_targeted_runs_are_marked_and_only_run_the_target(ws):
    put(ws, "test_mixed.py", MIXED)

    report, _ = run(ws, targets=["test_mixed.py::test_ok"])

    assert report.status == "PASSED"
    assert report.scope == "targeted" and report.targets == ["test_mixed.py::test_ok"]
    assert report.counts.total == 1
    assert "1 target(s)" in report.summary


def test_a_passing_target_does_not_stand_in_for_the_full_suite(ws):
    put(ws, "test_mixed.py", MIXED)

    targeted, _ = run(ws, targets=["test_mixed.py::test_ok"])
    full, _ = run(ws)

    assert targeted.status == "PASSED" and full.status == "FAILED"
    assert targeted.scope != full.scope


def test_keyword_selection_and_deselected_count(ws):
    put(ws, "test_mixed.py", MIXED)

    report, _ = run(ws, keyword="ok and not also")

    assert report.status == "PASSED"
    assert report.counts.total == 1
    assert report.counts.deselected == 3
    assert report.keyword == "ok and not also"


@pytest.mark.parametrize("target", ["--collect-only", "nope.py", "../outside.py"])
def test_unacceptable_targets_raise_before_anything_runs(ws, target):
    put(ws, "test_ok.py", "def test_a(): pass\n")

    with pytest.raises(TargetError):
        run(ws, targets=[target])

    assert list(ws.glob(".aseos_report_*")) == []


def test_keyword_is_validated_and_pytest_only(ws):
    put(ws, "test_ok.py", "def test_a(): pass\n")

    for bad in ("", "-x", "a\nb"):
        with pytest.raises(TargetError):
            run(ws, keyword=bad)

    put(ws, "test_u.py", "import unittest\nclass T(unittest.TestCase):\n    def test_x(self): pass\n")

    with pytest.raises(TargetError, match="only supported for pytest"):
        run(ws, framework="unittest", keyword="x")


# ------------------------------------------------------------- unittest


UNITTESTS = (
    "import unittest\n"
    "class T(unittest.TestCase):\n"
    "    def test_ok(self): self.assertTrue(True)\n"
    "    def test_fail(self): self.assertEqual(1, 2)\n"
    "    def test_error(self): raise RuntimeError('boom')\n"
    "    @unittest.skip('later')\n"
    "    def test_skipped(self): pass\n"
)


def test_unittest_run_from_console_output(ws):
    put(ws, "test_u.py", UNITTESTS)

    report, _ = run(ws)

    assert report.framework == "unittest" and report.report_source == "console"
    assert report.status == "FAILED"
    c = report.counts
    assert (c.total, c.passed, c.failed, c.errors, c.skipped) == (4, 1, 1, 1, 1)
    assert {x.name: x.status for x in report.cases}["test_ok"] == "passed"


def test_unittest_targets_are_dotted_names(ws):
    put(ws, "test_u.py", UNITTESTS)

    report, _ = run(ws, targets=["test_u.T.test_ok"])

    assert report.status == "PASSED" and report.scope == "targeted"
    assert report.counts.total == 1


def test_unittest_with_no_tests(ws):
    put(ws, "test_u.py", "import unittest\n")

    report, _ = run(ws, framework="unittest")

    assert report.status == "NO_TESTS"


# -------------------------------------------- unusual or broken outcomes


def test_timeout_is_not_a_failed_test(ws):
    put(ws, "test_slow.py", "import time\ndef test_slow(): time.sleep(60)\n")

    report, _ = run(ws, timeout=4)

    assert report.status == "TIMEOUT" and report.exit_code != 0
    assert not report.passed
    assert list(ws.glob(".aseos_report_*")) == []


def test_cancellation(ws):
    put(
        ws,
        "test_slow.py",
        "import pathlib, time\ndef test_slow():\n    pathlib.Path('started').write_text('1')\n    time.sleep(60)\n",
    )
    cancel = threading.Event()

    def stop_when_started():
        wait_for_file(ws / "started", 60)
        cancel.set()

    threading.Thread(target=stop_when_started, daemon=True).start()

    report, _ = run(ws, cancel=cancel)

    assert report.status == "CANCELLED"


CORRUPT_CONFTEST = (
    "import glob\n"
    "def pytest_unconfigure(config):\n"
    "    for path in glob.glob('.aseos_report_*.xml'):\n"
    "        open(path, 'w').write('<testsuite><testcase')\n"
)
DELETING_CONFTEST = (
    "import glob, os\n"
    "def pytest_unconfigure(config):\n"
    "    for path in glob.glob('.aseos_report_*.xml'):\n"
    "        os.remove(path)\n"
)


def test_a_corrupted_report_falls_back_to_the_console_and_says_so(ws):
    put(ws, "conftest.py", CORRUPT_CONFTEST)
    put(ws, "test_mixed.py", MIXED)

    report, _ = run(ws)

    assert report.status == "FAILED"
    assert report.report_source == "console"
    assert "corrupt" in report.report_problem
    assert (report.counts.passed, report.counts.failed) == (2, 1)
    assert report.cases == []  # only the console summary was usable


def test_a_missing_report_falls_back_to_the_console(ws):
    put(ws, "conftest.py", DELETING_CONFTEST)
    put(ws, "test_ok.py", "def test_a(): pass\n")

    report, _ = run(ws)

    assert report.status == "PASSED"
    assert report.report_source == "console"
    assert report.report_problem == "no JUnit report was produced"


def test_tests_that_modify_the_workspace_are_flagged(ws):
    put(ws, "test_writes.py", "def test_a():\n    open('generated.txt', 'w').write('x')\n")

    report, _ = run(ws)

    assert report.status == "PASSED"
    assert report.modified_during_run is True
    assert any("changed while the tests ran" in n for n in report.notes)


# --------------------------------------- status rules without processes


def fake_process(**kw):
    kw.setdefault("status", "PASS")
    kw.setdefault("exit_code", 0)
    return ProcessResult(**kw)


def fresh_report(**kw):
    kw.setdefault("status", "NOT_RUN")
    return RunReport(run_id="r", framework="pytest", **kw)


def decide(counts, process, problem=None, text=""):
    report = fresh_report(counts=counts, report_problem=problem)
    _decide_status(report, ADAPTERS["pytest"], process, text)
    return report


def test_exit_zero_without_results_is_unverified_not_passed():
    report = decide(None, fake_process(exit_code=0))

    assert report.status == "UNVERIFIED" and not report.passed
    assert "no results could be read" in report.summary


def test_a_crash_without_results_is_an_error():
    report = decide(None, fake_process(status="FAIL", exit_code=3))

    assert report.status == "ERROR" and "code 3" in report.summary


def test_passing_counts_with_a_failing_exit_code_are_not_trusted():
    report = decide(Counts(total=3, passed=3), fake_process(status="FAIL", exit_code=1))

    assert report.status == "ERROR"
    assert "exit code 1 although every reported test passed" in report.notes[0]


def test_an_inconsistent_report_cannot_produce_a_pass():
    report = decide(
        Counts(total=3, passed=3),
        fake_process(),
        problem="inconsistent: r.xml declares 9 tests but contains 3",
    )

    assert report.status == "ERROR" and "disagrees with itself" in report.notes[0]


def test_failures_win_over_errors_and_errors_over_exit_code():
    assert decide(Counts(total=3, passed=1, failed=1, errors=1), fake_process(status="FAIL", exit_code=1)).status == "FAILED"
    assert decide(Counts(total=2, passed=1, errors=1), fake_process(status="FAIL", exit_code=1)).status == "ERROR"


def test_a_missing_framework_is_not_run_rather_than_failed():
    text = "/usr/bin/python: No module named pytest"

    report = decide(None, fake_process(status="FAIL", exit_code=1), text=text)

    assert report.status == "NOT_RUN" and report.not_run_kind == "not_installed"
    assert "pytest" in report.not_run_reason


def test_process_level_outcomes_map_directly():
    for process_status, expected in (("TIMEOUT", "TIMEOUT"), ("CANCELLED", "CANCELLED")):
        assert decide(None, fake_process(status=process_status, exit_code=None)).status == expected

    start = decide(None, fake_process(status="ERROR", exit_code=None, error="Executable not found: python", error_kind="not_found"))
    sandbox = decide(None, fake_process(status="ERROR", exit_code=None, error="down", error_kind="sandbox_unavailable"))

    assert (start.status, start.not_run_kind) == ("NOT_RUN", "start_failed")
    assert sandbox.not_run_kind == "sandbox"


def test_runtime_result_conversion():
    passed = to_runtime_result(fresh_report(status="PASSED", counts=Counts(total=2, passed=2)))
    failed = to_runtime_result(fresh_report(status="FAILED", counts=Counts(total=3, passed=1, failed=1, errors=1)))
    odd = to_runtime_result(fresh_report(status="NO_TESTS"))

    assert (passed.status, passed.passed, passed.failed) == ("PASS", 2, 0)
    assert (failed.status, failed.passed, failed.failed) == ("FAIL", 1, 2)
    assert odd.status == "ERROR"


# ----------------------------------------------- refusals and detection


def test_no_framework_is_not_run(ws):
    report, process = run(ws)

    assert report.status == "NOT_RUN" and report.not_run_kind == "no_framework"
    assert process is None and report.command == []


def test_explicit_framework_without_configuration_is_allowed_but_noted(ws):
    report, _ = run(ws, framework="unittest")

    assert report.framework == "unittest"
    assert any("no matching configuration" in e for e in report.evidence)


def test_unknown_framework_name(ws):
    with pytest.raises(TargetError):
        run(ws, framework="mocha-ish")


def test_the_command_policy_still_applies(ws):
    put(ws, "test_ok.py", "def test_a(): pass\n")

    report, process = run(ws, policy=CommandPolicy())

    assert report.status == "NOT_RUN" and report.not_run_kind == "policy"
    assert process is None
    assert report.command[:3] == ["python", "-m", "pytest"]


def test_other_detected_frameworks_are_mentioned(ws):
    put(ws, "test_ok.py", "def test_a(): pass\n")
    put(ws, "go.mod", "module x\n")

    report, _ = run(ws)

    assert report.framework == "pytest"
    assert any("Also detected: go" in n for n in report.notes)


# --------------------------------------------- results and versions


def test_results_record_the_version_that_was_tested(ws):
    put(ws, "test_ok.py", "def test_a(): pass\n")

    report, _ = run(ws)

    assert len(report.workspace_version["fingerprint"]) == 64
    assert report.workspace_version["kind"] == "files"
    assert report.started_at <= report.finished_at and report.duration_ms >= 0


# ----------------------------------------------------- through the gateway


def gateway(ws, caps=DEFAULT_CAPABILITIES, **kw):
    return ExecutionGateway(str(ws), tools=default_tools(), granted_capabilities=caps, **kw)


async def call(gw, action_type, payload=None):
    return await gw.execute(Action(id="t-1", type=action_type, payload=payload or {}))


@pytest.mark.asyncio
async def test_test_run_tool_success_carries_the_full_report(ws):
    put(ws, "test_ok.py", "def test_a(): pass\n")

    result = await call(gateway(ws), "RUN_TEST")

    assert result.success is True, result.stderr
    assert result.tool == "test.run"
    assert result.data["status"] == "PASSED"
    assert result.data["counts"]["passed"] == 1
    assert result.data["workspace_version"]["fingerprint"]
    assert result.data["process_tree"] == {} and result.data["sandboxed"] is False


@pytest.mark.asyncio
async def test_test_run_tool_failures_are_categorized_by_what_happened(ws):
    gw = gateway(ws)
    put(ws, "test_mixed.py", MIXED)
    failed = await call(gw, "RUN_TEST")

    (ws / "test_mixed.py").unlink()
    put(ws, "pytest.ini", "[pytest]\n")
    put(ws, "test_empty.py", "x = 1\n")
    empty = await call(gw, "RUN_TEST")

    (ws / "pytest.ini").unlink()
    (ws / "test_empty.py").unlink()
    nothing = await call(gw, "RUN_TEST")

    assert (failed.error_category, failed.data["status"], failed.exit_code) == ("TEST_FAILURE", "FAILED", 1)
    assert "math is hard" in failed.data["cases"][0]["message"]
    assert (empty.error_category, empty.data["status"]) == ("TEST_FAILURE", "NO_TESTS")
    assert (nothing.error_category, nothing.data["status"]) == ("INVALID_ARGUMENTS", "NOT_RUN")


@pytest.mark.asyncio
async def test_test_run_tool_arguments(ws):
    put(ws, "test_mixed.py", MIXED)
    gw = gateway(ws)

    targeted = await call(gw, "RUN_TEST", {"targets": ["test_mixed.py::test_ok"]})
    bad_target = await call(gw, "RUN_TEST", {"targets": ["../x.py"]})
    bad_framework = await call(gw, "RUN_TEST", {"framework": "rspec"})
    option = await call(gw, "RUN_TEST", {"targets": ["--maxfail=1"]})

    assert targeted.success and targeted.data["scope"] == "targeted"
    assert bad_target.error_category == "INVALID_ARGUMENTS"
    assert bad_framework.error_category == "INVALID_ARGUMENTS"
    assert option.error_category == "INVALID_ARGUMENTS"


@pytest.mark.asyncio
async def test_timeout_through_the_tool(ws):
    put(ws, "test_slow.py", "import time\ndef test_slow(): time.sleep(60)\n")

    result = await call(gateway(ws), "RUN_TEST", {"timeout_seconds": 4})

    assert result.error_category == "TIMEOUT" and result.data["status"] == "TIMEOUT"


@pytest.mark.asyncio
async def test_a_result_goes_stale_when_the_code_changes(ws):
    put(ws, "test_ok.py", "def test_a(): assert True\n")
    gw = gateway(ws)

    run_result = await call(gw, "RUN_TEST")
    fingerprint = run_result.data["workspace_version"]["fingerprint"]
    fresh = await call(gw, "TEST_FRESHNESS", {"fingerprint": fingerprint})
    put(ws, "test_ok.py", "def test_a(): assert False\n")
    stale = await call(gw, "TEST_FRESHNESS", {"fingerprint": fingerprint})
    put(ws, "test_ok.py", "def test_a(): assert True\n")
    restored = await call(gw, "TEST_FRESHNESS", {"fingerprint": fingerprint})

    assert fresh.data["current"] is True
    assert stale.data["current"] is False and "stale" in stale.stdout
    assert restored.data["current"] is True  # same content, same version


@pytest.mark.asyncio
async def test_freshness_validates_its_argument_and_is_read_only(ws):
    result = await call(gateway(ws, {"fs.read"}), "TEST_FRESHNESS", {"fingerprint": "abc"})
    ok = await call(gateway(ws, {"fs.read"}), "TEST_FRESHNESS", {"fingerprint": "0" * 64})

    assert result.error_category == "INVALID_ARGUMENTS"
    assert ok.success is True and ok.data["current"] is False


@pytest.mark.asyncio
async def test_cancel_through_the_gateway_reports_cancelled(ws):
    put(
        ws,
        "test_slow.py",
        "import pathlib, time\ndef test_slow():\n    pathlib.Path('started').write_text('1')\n    time.sleep(60)\n",
    )
    gw = gateway(ws)
    task = asyncio.create_task(gw.execute(Action(id="slow", type="RUN_TEST", payload={})))

    assert await asyncio.to_thread(wait_for_file, ws / "started", 60)
    gw.cancel("slow")
    result = await asyncio.wait_for(task, 60)

    assert result.error_category == "CANCELLED"
    assert result.data["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_a_required_sandbox_that_is_missing_refuses_test_runs(ws):
    put(ws, "test_ok.py", "def test_a(): open('ran_on_host', 'w').write('x')\n")
    gw = ExecutionGateway(str(ws), tools=default_tools(), profile="dev")

    result = await call(gw, "RUN_TEST")

    assert result.error_category == "PERMISSION_DENIED"
    assert not (ws / "ran_on_host").exists()


def test_path_import_is_used():
    assert Path(__file__).exists()
