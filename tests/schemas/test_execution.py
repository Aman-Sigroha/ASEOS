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
