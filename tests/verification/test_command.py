import pytest

from runtime.schemas.task import Task
from runtime.state.state import AgentState
from runtime.verification.command import CommandVerifier


def make_state(tmp_path) -> AgentState:
    return AgentState(
        task=Task(
            id="task-001",
            description="Test verification",
            workspace_path=str(tmp_path),
        ),
    )


@pytest.mark.asyncio
async def test_command_verifier_passes_successful_command(tmp_path):
    verifier = CommandVerifier(
        commands=["python -c \"print('verification ok')\""],
    )

    result = await verifier.verify(make_state(tmp_path))

    assert result.status == "PASS"
    assert len(result.checks) == 1
    assert result.checks[0].status == "PASS"
    assert "verification ok" in result.checks[0].message


@pytest.mark.asyncio
async def test_command_verifier_fails_failed_command(tmp_path):
    verifier = CommandVerifier(
        commands=["python -c \"print('verification failed'); raise SystemExit(1)\""],
    )

    result = await verifier.verify(make_state(tmp_path))

    assert result.status == "FAIL"
    assert len(result.checks) == 1
    assert result.checks[0].status == "FAIL"


@pytest.mark.asyncio
async def test_command_verifier_runs_multiple_commands(tmp_path):
    verifier = CommandVerifier(
        commands=[
            "python -c \"print('check one')\"",
            "python -c \"print('check two')\"",
        ],
    )

    result = await verifier.verify(make_state(tmp_path))

    assert result.status == "PASS"
    assert len(result.checks) == 2
    assert all(check.status == "PASS" for check in result.checks)


@pytest.mark.asyncio
async def test_command_verifier_reports_mixed_results(tmp_path):
    verifier = CommandVerifier(
        commands=[
            "python -c \"print('pass')\"",
            'python -c "raise SystemExit(1)"',
        ],
    )

    result = await verifier.verify(make_state(tmp_path))

    assert result.status == "FAIL"
    assert len(result.checks) == 2
    assert result.checks[0].status == "PASS"
    assert result.checks[1].status == "FAIL"


@pytest.mark.asyncio
async def test_command_verifier_times_out(tmp_path):
    verifier = CommandVerifier(
        commands=[
            'python -c "import time; time.sleep(2)"',
        ],
        timeout_seconds=0.1,
    )

    result = await verifier.verify(make_state(tmp_path))

    assert result.status == "TIMEOUT"
    assert result.checks[0].status == "TIMEOUT"


@pytest.mark.asyncio
async def test_command_verifier_handles_empty_commands(tmp_path):
    verifier = CommandVerifier(commands=[])

    result = await verifier.verify(make_state(tmp_path))

    assert result.status == "ERROR"
    assert result.checks == []


def test_command_verifier_rejects_invalid_timeout():

    with pytest.raises(ValueError):
        CommandVerifier(
            commands=["python -c \"print('ok')\""],
            timeout_seconds=0,
        )
