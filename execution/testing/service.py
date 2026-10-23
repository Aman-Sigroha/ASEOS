"""Run a workspace's tests and turn the outcome into a RunReport.

The report is built from evidence in this order: a structured report the
framework wrote (JUnit XML / `go test -json`), else the framework's console
summary, else the exit code alone. It never reports PASSED on the exit code
alone: with no readable results the status is UNVERIFIED.
"""

import re
import threading
import time
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from execution.commands.policy import CommandPolicy
from execution.commands.process import ProcessLimits, ProcessResult, run_process
from execution.testing import console
from execution.testing.adapters import ADAPTERS, Detection, TargetError, detect_all
from execution.testing.junit import parse_junit
from execution.testing.model import CaseResult, Counts, RunReport
from execution.versioning import workspace_version

MAX_CASES = 200
MAX_KEYWORD_CHARS = 200
_MISSING_MODULE = re.compile(r"No module named '?([\w.]+)'?")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _order(cases: list[CaseResult]) -> list[CaseResult]:
    rank = {"failed": 0, "error": 0, "skipped": 1, "passed": 2}
    return sorted(cases, key=lambda c: rank[c.status])


def _not_run(report: RunReport, kind: str, reason: str) -> RunReport:
    report.status = "NOT_RUN"
    report.not_run_kind = kind
    report.not_run_reason = reason
    report.summary = f"NOT_RUN: {reason}"
    return report


def _validate_keyword(keyword: str | None, framework: str) -> None:
    if keyword is None:
        return

    if framework != "pytest":
        raise TargetError("keyword filters are only supported for pytest")

    if (
        not keyword.strip()
        or len(keyword) > MAX_KEYWORD_CHARS
        or keyword.startswith("-")
        or "\n" in keyword
        or "\0" in keyword
    ):
        raise TargetError("Invalid keyword expression")


def _summary(report: RunReport) -> str:
    counts = report.counts
    scope = "full suite" if report.scope == "full" else f"{len(report.targets)} target(s)"
    detail = ""

    if counts is not None:
        parts = [f"{counts.passed} passed"]

        for label, value in (
            ("failed", counts.failed),
            ("errors", counts.errors),
            ("skipped", counts.skipped),
        ):
            if value:
                parts.append(f"{value} {label}")

        if counts.deselected:
            parts.append(f"{counts.deselected} deselected")

        detail = ": " + ", ".join(parts)

    return f"{report.status}{detail} ({report.framework}, {scope})"


def _collect_reports(
    adapter, ws: Path, report_name: str | None, started_ts: float
) -> list[Path]:
    if adapter.report_kind == "junit-file" and report_name:
        path = ws / report_name
        return [path] if path.is_file() else []

    if adapter.report_kind == "junit-dir":
        paths: list[Path] = []

        for directory in adapter.junit_dirs(ws):
            for xml in directory.glob("*.xml"):
                try:
                    fresh = xml.stat().st_mtime >= started_ts - 2.0
                except OSError:
                    continue

                if fresh:  # never trust a report left by an earlier run
                    paths.append(xml)

        return sorted(paths)

    return []


def run_test_suite(
    workspace: str | Path,
    *,
    framework: str = "auto",
    targets: Sequence[str] = (),
    keyword: str | None = None,
    timeout: float = 120.0,
    cancel: threading.Event | None = None,
    sandbox: Any = None,
    policy: CommandPolicy | None = None,
    capabilities: frozenset[str] | set[str] = frozenset(),
    log_dir: Path | None = None,
    on_start: Any = None,
) -> tuple[RunReport, ProcessResult | None]:
    """Detect, run and normalize. Raises TargetError for unacceptable targets."""

    ws = Path(workspace).resolve()
    targets = list(targets)
    run_id = f"test-{uuid.uuid4().hex[:12]}"
    report = RunReport(
        run_id=run_id,
        framework=framework,
        cwd=str(ws),
        scope="targeted" if targets else "full",
        targets=targets,
        keyword=keyword,
        status="NOT_RUN",
        started_at=_now(),
    )

    # ---- choose the framework -------------------------------------------
    detections = detect_all(ws)

    if framework == "auto":
        if not detections:
            report.finished_at = _now()
            return _not_run(
                report,
                "no_framework",
                "No test framework was detected (looked for pytest/unittest, "
                "Maven, Gradle, npm test, Cargo and Go).",
            ), None

        chosen = detections[0]
        report.framework = chosen.framework

        if len(detections) > 1:
            report.notes.append(
                "Also detected: " + ", ".join(d.framework for d in detections[1:])
                + "; pass framework=... to run those instead."
            )
    else:
        if framework not in ADAPTERS:
            raise TargetError(f"Unknown framework: {framework!r}")

        chosen = next((d for d in detections if d.framework == framework), None)

        if chosen is None:
            chosen = Detection(
                framework,
                ["selected explicitly; no matching configuration was found"],
            )

    adapter = ADAPTERS[chosen.framework]
    report.evidence = list(chosen.evidence)
    report.notes += chosen.notes
    adapter.validate_targets(ws, targets)
    _validate_keyword(keyword, adapter.name)

    # ---- build and check the command ------------------------------------
    version_before = workspace_version(ws)
    report.workspace_version = version_before
    report_name = f".aseos_report_{run_id}.xml" if adapter.report_kind == "junit-file" else None
    command = adapter.command(ws, targets, keyword, report_name)
    report.command = command
    decision = (policy or CommandPolicy.default()).evaluate(
        command, ws, ws, frozenset(capabilities)
    )

    if not decision.allowed:
        report.finished_at = _now()
        return _not_run(report, "policy", f"refused by the command policy: {decision.reason}"), None

    # ---- run -------------------------------------------------------------
    started_ts = time.time()
    process = run_process(
        command,
        ws,
        env_extra={"CI": "1", "PYTHONDONTWRITEBYTECODE": "1"},
        limits=ProcessLimits(timeout_seconds=timeout),
        cancel=cancel,
        workspace=ws,
        sandbox=sandbox,
        log_dir=log_dir,
        on_start=on_start,
    )
    report.finished_at = _now()
    report.duration_ms = process.duration_ms
    report.exit_code = process.exit_code
    report.process = {
        "run_id": process.run_id,
        "output_files": process.output_files,
        "stdout_bytes": process.stdout_total,
        "stderr_bytes": process.stderr_total,
        "truncated": process.truncated,
        "tree": process.tree,
    }
    text = process.stdout + "\n" + process.stderr
    report_paths = _collect_reports(adapter, ws, report_name, started_ts)

    try:
        _read_results(report, adapter, process, text, report_paths)
    finally:
        if report_name:
            (ws / report_name).unlink(missing_ok=True)

    version_after = workspace_version(ws)
    report.modified_during_run = (
        version_after["fingerprint"] != version_before["fingerprint"]
    )

    if report.modified_during_run:
        report.notes.append(
            "The working tree changed while the tests ran (they may generate "
            "or modify files); workspace_version is the state before the run."
        )

    _decide_status(report, adapter, process, text)
    report.summary = report.summary or _summary(report)
    return report, process


def _read_results(report, adapter, process, text, report_paths) -> None:
    counts: Counts | None = None
    cases: list[CaseResult] = []
    parsed_console = None

    if adapter.report_kind in ("junit-file", "junit-dir"):
        if report_paths:
            parsed = parse_junit(report_paths)
            report.report_problem = "; ".join(parsed.problems) or None
            report.report_files = parsed.files_read

            if parsed.files_read:
                counts, cases = parsed.counts(), parsed.cases
                report.report_source = "junit"
        elif process.status in ("PASS", "FAIL"):
            report.report_problem = "no JUnit report was produced"

    elif adapter.report_kind == "json":
        parsed_json, problems = console.parse_go_json(process.stdout)
        report.report_problem = "; ".join(problems) or None

        if parsed_json is not None:
            counts, cases = parsed_json
            report.report_source = "json"

    if adapter.report_kind != "json":
        parsed_console = adapter.parse_console(text)

    if counts is None and parsed_console is not None:
        counts, cases = parsed_console
        report.report_source = "console"
    elif counts is not None and parsed_console is not None:
        counts.deselected = parsed_console[0].deselected

    report.counts = counts
    report.cases = _order(cases)[:MAX_CASES]
    report.cases_truncated = len(cases) > MAX_CASES


def _decide_status(report: RunReport, adapter, process: ProcessResult, text: str) -> None:
    counts, exit_code = report.counts, process.exit_code

    if process.status == "ERROR":
        kind = {
            "sandbox_unavailable": "sandbox",
            "sandbox_refused": "sandbox",
        }.get(process.error_kind or "", "start_failed")
        _not_run(report, kind, process.error or "the test command could not be started")
        return

    if process.status == "TIMEOUT":
        report.status = "TIMEOUT"
        return

    if process.status == "CANCELLED":
        report.status = "CANCELLED"
        return

    missing = _MISSING_MODULE.search(text) if counts is None else None

    if missing and exit_code not in (0, None):
        _not_run(
            report,
            "not_installed",
            f"the test framework is not installed in the interpreter that was "
            f"used (No module named {missing.group(1)!r})",
        )
        return

    problems = report.report_problem or ""

    if counts is None:
        if exit_code == 0:
            report.status = "UNVERIFIED"
            report.summary = (
                f"UNVERIFIED: {report.framework} exited 0 but no results could "
                "be read (report missing, unreadable or unrecognized)"
            )
        elif exit_code in adapter.no_tests_exit_codes():
            report.status = "NO_TESTS"
        else:
            report.status = "ERROR"
            report.summary = (
                f"ERROR: {report.framework} exited with code {exit_code} and "
                "produced no readable results"
            )

        return

    if counts.total == 0:
        ok_exit = exit_code == 0 or exit_code in adapter.no_tests_exit_codes()
        report.status = "NO_TESTS" if ok_exit else "ERROR"
        return

    if counts.failed > 0:
        report.status = "FAILED"
    elif counts.errors > 0:
        report.status = "ERROR"
    elif counts.passed == 0 and counts.skipped == counts.total:
        report.status = "NO_TESTS"
        report.summary = f"NO_TESTS: all {counts.total} tests were skipped, so nothing was verified ({report.framework})"
    elif exit_code != 0:
        report.status = "ERROR"
        report.notes.append(
            f"exit code {exit_code} although every reported test passed"
        )
    elif "inconsistent" in problems:
        report.status = "ERROR"
        report.notes.append("the report disagrees with itself; its results are not trusted")
    else:
        report.status = "PASSED"
