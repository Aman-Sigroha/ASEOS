from typing import Any, Literal

from pydantic import BaseModel, Field


VerificationStatus = Literal[
    "PASS",
    "FAIL",
    "ERROR",
    "TIMEOUT",
]


class VerificationCheck(BaseModel):
    name: str = Field(min_length=1)
    status: VerificationStatus
    message: str = ""
    duration_ms: int | None = Field(default=None, ge=0)
    # Optional, produced by the execution layer (Role 4): which kind of check
    # this was and the structured evidence behind the status.
    kind: str | None = None
    evidence: dict[str, Any] | None = None


class VerificationResult(BaseModel):
    status: VerificationStatus
    checks: list[VerificationCheck] = Field(default_factory=list)
    summary: str = Field(min_length=1)
    # Optional run-level evidence (run id, workspace version, next steps).
    evidence: dict[str, Any] | None = None
