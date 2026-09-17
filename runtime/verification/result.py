from typing import Literal

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


class VerificationResult(BaseModel):
    status: VerificationStatus
    checks: list[VerificationCheck] = Field(default_factory=list)
    summary: str = Field(min_length=1)
