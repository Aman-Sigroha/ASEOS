import pytest
from pydantic import ValidationError

from runtime.verification.result import (
    VerificationCheck,
    VerificationResult,
)


def test_verification_check_creation():

    check = VerificationCheck(
        name="unit-tests",
        status="PASS",
        message="All unit tests passed.",
        duration_ms=120,
    )

    assert check.name == "unit-tests"
    assert check.status == "PASS"
    assert check.message == "All unit tests passed."
    assert check.duration_ms == 120


def test_verification_result_creation():

    result = VerificationResult(
        status="PASS",
        checks=[
            VerificationCheck(
                name="unit-tests",
                status="PASS",
                message="All tests passed.",
            )
        ],
        summary="Verification passed.",
    )

    assert result.status == "PASS"
    assert len(result.checks) == 1
    assert result.checks[0].name == "unit-tests"
    assert result.summary == "Verification passed."


def test_verification_result_allows_multiple_checks():

    result = VerificationResult(
        status="FAIL",
        checks=[
            VerificationCheck(
                name="unit-tests",
                status="PASS",
            ),
            VerificationCheck(
                name="integration-tests",
                status="FAIL",
                message="Authentication test failed.",
            ),
        ],
        summary="Verification failed.",
    )

    assert len(result.checks) == 2
    assert result.checks[0].status == "PASS"
    assert result.checks[1].status == "FAIL"


def test_verification_check_rejects_invalid_status():

    with pytest.raises(ValidationError):
        VerificationCheck(
            name="unit-tests",
            status="UNKNOWN",
        )


def test_verification_check_rejects_empty_name():

    with pytest.raises(ValidationError):
        VerificationCheck(
            name="",
            status="PASS",
        )


def test_verification_result_rejects_empty_summary():

    with pytest.raises(ValidationError):
        VerificationResult(
            status="PASS",
            summary="",
        )
