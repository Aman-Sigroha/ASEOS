from typing import Literal

from pydantic import BaseModel, Field


class ExecutionResult(BaseModel):
    """Result returned by the execution layer."""

    action_id: str = Field(min_length=1)
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_ms: int | None = Field(default=None, ge=0)


class TestRunResult(BaseModel):
    """Normalized result of a test execution."""

    __test__ = False

    status: Literal["PASS", "FAIL", "TIMEOUT", "ERROR"]
    passed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)


class TaskResult(BaseModel):
    """Final result of an engineering task."""

    task_id: str = Field(min_length=1)
    status: Literal["COMPLETED", "FAILED", "ABORTED"]
    summary: str
    changed_files: list[str] = Field(default_factory=list)
    test_result: TestRunResult | None = None
