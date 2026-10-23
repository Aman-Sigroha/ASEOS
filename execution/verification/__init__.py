"""Deterministic verification evidence (Role 4). See docs/EXECUTION_CONTRACT.md §2.8."""

from execution.verification.checks import (
    CHECK_KINDS,
    Check,
    CommandCheck,
    DiffContainsCheck,
    FileContentCheck,
    FileExistsCheck,
    NoProhibitedPathsCheck,
    TestsCheck,
    parse_checks,
)
from execution.verification.evidence import CheckEvidence, VerificationReport
from execution.verification.service import fold_status, report_is_current, run_checks
from execution.verification.verifier import ExecutionVerifier, to_verification_result

__all__ = [
    "CHECK_KINDS",
    "Check",
    "CheckEvidence",
    "CommandCheck",
    "DiffContainsCheck",
    "ExecutionVerifier",
    "FileContentCheck",
    "FileExistsCheck",
    "NoProhibitedPathsCheck",
    "TestsCheck",
    "VerificationReport",
    "fold_status",
    "parse_checks",
    "report_is_current",
    "run_checks",
    "to_verification_result",
]
