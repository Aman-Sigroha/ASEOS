"""Evidence records produced by verification.

`VerificationReport` is the full, traceable record (what was checked, which
tool calls established it, against which workspace version). Its `status` is
folded into Role 1's `VerificationStatus`, which stays the single
authoritative verdict (docs/EXECUTION_CONTRACT.md §2.8).
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from runtime.schemas.execution import ArtifactRef
from runtime.verification.result import VerificationStatus

NextStep = Literal[
    "inspect",  # read the failing output / files
    "retrieve_output",  # more output is available (artifacts / spool files)
    "retest_targeted",  # re-run only the failing tests
    "modify_patch",  # change the code and verify again
    "replan",  # the approach may be wrong
    "stop",  # cannot proceed without a human or a different grant
]

EXCERPT_LIMIT = 3000


def excerpt(text: str, limit: int = EXCERPT_LIMIT) -> str:
    """Head and tail of long text; failures usually end with the useful part."""

    text = text.strip()

    if len(text) <= limit:
        return text

    head = limit // 3
    tail = limit - head
    omitted = len(text) - head - tail
    return f"{text[:head]}\n... [{omitted} characters omitted] ...\n{text[-tail:]}"


class ToolTrace(BaseModel):
    """One gateway call that contributed to a check."""

    tool: str | None = None
    action_id: str
    success: bool
    exit_code: int | None = None
    error_category: str | None = None
    duration_ms: int | None = None
    truncated: bool = False
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    stdout_excerpt: str = ""
    stderr_excerpt: str = ""


class CheckEvidence(BaseModel):
    index: int
    kind: str
    name: str
    status: VerificationStatus
    message: str = ""
    duration_ms: int = 0
    observed: dict[str, Any] = Field(default_factory=dict)
    traces: list[ToolTrace] = Field(default_factory=list)
    next_steps: list[NextStep] = Field(default_factory=list)
    workspace_version: dict[str, Any] | None = None
    """Set for checks that run programs (they may change the workspace)."""
    modified_workspace: bool = False


class VerificationReport(BaseModel):
    run_id: str
    status: VerificationStatus
    summary: str
    checks: list[CheckEvidence] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    """Checks not run because `fail_fast` stopped early."""
    workspace: str = ""
    version_before: dict[str, Any] = Field(default_factory=dict)
    version_after: dict[str, Any] = Field(default_factory=dict)
    stable: bool | None = None
    """True when the workspace was identical before and after; None when it
    could not be fingerprinted."""
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0

    @property
    def passed(self) -> bool:
        return self.status == "PASS"

    @property
    def next_steps(self) -> list[NextStep]:
        seen: list[NextStep] = []

        for check in self.checks:
            for step in check.next_steps:
                if step not in seen:
                    seen.append(step)

        return seen
