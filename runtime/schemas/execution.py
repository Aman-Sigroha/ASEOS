from typing import Literal

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


ErrorCategory = Literal[
    "INVALID_ARGUMENTS",
    "UNKNOWN_TOOL",
    "PERMISSION_DENIED",
    "WORKSPACE_UNAVAILABLE",
    "RESOURCE_LIMIT",
    "TIMEOUT",
    "CANCELLED",
    "PROCESS_FAILURE",
    "PATCH_CONFLICT",
    "GIT_FAILURE",
    "TEST_FAILURE",
    "INTERNAL_ERROR",
]


class ArtifactRef(BaseModel):
    """Reference to a large output stored outside the result."""

    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    description: str = ""
    # Set for artifacts held by the execution artifact store.
    size: int | None = Field(default=None, ge=0)
    sha256: str | None = None
    retention: str | None = None
    truncated: bool = False
    redactions: int = 0


class ExecutionResult(BaseModel):
    """Result returned by the execution layer.

    The first six fields are the original baseline contract. The remaining
    fields are optional extensions with defaults, so existing producers and
    consumers keep working.
    """

    action_id: str = Field(min_length=1)
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_ms: int | None = Field(default=None, ge=0)

    # Extensions (see docs/EXECUTION_CONTRACT.md).
    tool: str | None = None
    error_category: ErrorCategory | None = None
    truncated: bool = False
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _success_has_no_error_category(self) -> "ExecutionResult":
        if self.success and self.error_category is not None:
            raise ValueError("A successful result cannot have an error_category.")

        return self


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
