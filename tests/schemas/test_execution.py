from runtime.schemas.execution import (
    ExecutionResult,
    TaskResult,
    TestRunResult,
)


def test_execution_result():
    result = ExecutionResult(
        action_id="action-001",
        success=True,
        stdout="tests passed",
        exit_code=0,
        duration_ms=100,
    )

    assert result.success is True
    assert result.exit_code == 0


def test_task_result():
    result = TaskResult(
        task_id="task-001",
        status="COMPLETED",
        summary="Bug fixed",
        changed_files=["src/calculator.py"],
        test_result=TestRunResult(
            status="PASS",
            passed=4,
            failed=0,
        ),
    )

    assert result.status == "COMPLETED"
    assert result.test_result.passed == 4


def test_execution_result_extensions_default_to_baseline():
    result = ExecutionResult(action_id="a", success=True)

    assert result.tool is None
    assert result.error_category is None
    assert result.truncated is False
    assert result.artifacts == []
    assert result.data == {}


def test_execution_result_rejects_error_category_on_success():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ExecutionResult(
            action_id="a", success=True, error_category="TIMEOUT"
        )


def test_execution_result_accepts_failure_with_category():
    result = ExecutionResult(
        action_id="a", success=False, error_category="PATCH_CONFLICT"
    )

    assert result.error_category == "PATCH_CONFLICT"
