"""Normalized result of one test run, independent of framework."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from runtime.schemas.execution import TestRunResult

RunStatus = Literal[
    "PASSED",  # ran, at least one test, all passed, exit code agrees
    "FAILED",  # one or more tests failed
    "ERROR",  # ran, but crashed / collection or setup errors / inconsistent
    "NO_TESTS",  # ran, but nothing was collected
    "UNVERIFIED",  # exit code 0 but no readable results
    "TIMEOUT",
    "CANCELLED",
    "NOT_RUN",  # could not start (no framework, policy, missing program, ...)
]

CaseStatus = Literal["passed", "failed", "error", "skipped"]


class CaseResult(BaseModel):
    name: str
    classname: str = ""
    file: str = ""
    status: CaseStatus
    duration_s: float | None = None
    message: str = ""


class Counts(BaseModel):
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    deselected: int | None = None  # only known from console output


class RunReport(BaseModel):
    run_id: str
    framework: str
    command: list[str] = Field(default_factory=list)
    cwd: str = ""
    scope: Literal["full", "targeted"] = "full"
    targets: list[str] = Field(default_factory=list)
    keyword: str | None = None
    status: RunStatus
    summary: str = ""
    exit_code: int | None = None
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0
    counts: Counts | None = None  # None = no readable results
    cases: list[CaseResult] = Field(default_factory=list)
    cases_truncated: bool = False
    report_source: Literal["junit", "json", "console", "none"] = "none"
    report_files: list[str] = Field(default_factory=list)
    report_problem: str | None = None
    workspace_version: dict[str, Any] = Field(default_factory=dict)
    modified_during_run: bool = False
    evidence: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    # Frameworks detected in the workspace that this run did NOT cover (auto
    # mode runs the first one only). A pass says nothing about them.
    other_frameworks: list[str] = Field(default_factory=list)
    not_run_reason: str | None = None
    not_run_kind: str | None = None  # no_framework | policy | sandbox | start_failed | not_installed
    process: dict[str, Any] = Field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == "PASSED"


def to_runtime_result(report: RunReport) -> TestRunResult:
    """The compact result type Role 1's runtime already understands."""

    status = {
        "PASSED": "PASS",
        "FAILED": "FAIL",
        "TIMEOUT": "TIMEOUT",
    }.get(report.status, "ERROR")
    counts = report.counts or Counts()
    return TestRunResult(
        status=status,  # type: ignore[arg-type]
        passed=counts.passed,
        failed=counts.failed + counts.errors,
    )
