"""ExecutionVerifier: Role 1's Verifier backed by execution evidence."""

import json

import pytest

from execution.gateway import ExecutionGateway
from execution.gateway.tools import DEFAULT_CAPABILITIES, default_tools
from execution.tests.gitfixtures import init_repo, write
from execution.verification import (
    ExecutionVerifier,
    FileContentCheck,
    FileExistsCheck,
    NoProhibitedPathsCheck,
    TestsCheck,
    to_verification_result,
)
from runtime.agent.mock_executor import MockActionExecutor
from runtime.agent.runtime import AgentRuntime
from runtime.llm.mock import MockLLMClient
from runtime.planner.actions import ActionGenerator
from runtime.planner.planner import Planner
from runtime.schemas.repository import RepositoryContext
from runtime.schemas.task import Task
from runtime.state.state import AgentState
from runtime.task.understanding import TaskUnderstandingService
from runtime.verification.result import VerificationResult


@pytest.fixture
def repo(tmp_path):
    return init_repo(tmp_path / "repo")


def gateway(repo):
    return ExecutionGateway(
        str(repo), tools=default_tools(), granted_capabilities=DEFAULT_CAPABILITIES
    )


def state_for(path) -> AgentState:
    return AgentState(
        task=Task(id="task-1", description="verify", workspace_path=str(path))
    )


@pytest.mark.asyncio
async def test_passing_checks_become_a_pass_with_evidence(repo):
    write(repo, "test_ok.py", "def test_a(): assert True\n")
    verifier = ExecutionVerifier(
        gateway(repo),
        [TestsCheck(), FileExistsCheck(path="a.txt")],
    )

    result = await verifier.verify(state_for(repo))

    assert isinstance(result, VerificationResult)
    assert result.status == "PASS"
    assert [c.kind for c in result.checks] == ["tests", "file_exists"]
    assert result.checks[0].evidence["observed"]["counts"]["passed"] == 1
    assert result.checks[0].evidence["traces"][0]["tool"] == "test.run"
    assert result.evidence["stable"] is True
    assert result.evidence["version_before"] == result.evidence["version_after"]
    assert verifier.last_report is not None and verifier.last_report.passed


@pytest.mark.asyncio
async def test_failure_carries_next_steps_for_the_replanner(repo):
    write(repo, "test_bad.py", "def test_bad(): assert 1 == 2, 'wrong'\n")
    verifier = ExecutionVerifier(gateway(repo), [TestsCheck()])

    result = await verifier.verify(state_for(repo))

    assert result.status == "FAIL"
    steps = result.evidence["next_steps"]
    assert steps[:2] == ["inspect", "retest_targeted"]
    assert result.checks[0].evidence["observed"]["failing_cases"][0]["name"] == "test_bad"
    assert "wrong" in result.checks[0].message


@pytest.mark.asyncio
async def test_the_result_is_plain_json_and_round_trips_through_role_1_schema(repo):
    write(repo, "test_bad.py", "def test_bad(): assert False\n")
    verifier = ExecutionVerifier(gateway(repo), [TestsCheck(), FileExistsCheck(path="zz")])

    result = await verifier.verify(state_for(repo))
    payload = json.loads(result.model_dump_json())
    again = VerificationResult.model_validate(payload)

    assert again == result


@pytest.mark.asyncio
async def test_a_role_1_result_without_evidence_is_still_valid():
    # Existing producers (CommandVerifier, test doubles) do not set the new fields.
    result = VerificationResult(status="PASS", summary="ok")

    assert result.evidence is None and result.checks == []


@pytest.mark.asyncio
async def test_a_verifier_bound_to_another_workspace_refuses(repo, tmp_path):
    other = init_repo(tmp_path / "other")
    verifier = ExecutionVerifier(gateway(repo), [FileExistsCheck(path="a.txt")])

    result = await verifier.verify(state_for(other))

    assert result.status == "ERROR" and "different workspace" in result.summary
    assert result.checks == [] and verifier.last_report is None


@pytest.mark.asyncio
async def test_checks_can_be_derived_from_the_state(repo):
    seen = []

    def checks(state):
        seen.append(state.task.id)
        return [FileExistsCheck(path="a.txt")]

    result = await ExecutionVerifier(gateway(repo), checks).verify(state_for(repo))

    assert result.status == "PASS" and seen == ["task-1"]


@pytest.mark.asyncio
async def test_long_messages_are_bounded_for_the_runtime(repo):
    write(repo, "test_noisy.py", "def test_a():\n    assert False, 'x' * 100000\n")

    result = await ExecutionVerifier(gateway(repo), [TestsCheck()]).verify(state_for(repo))

    assert len(result.checks[0].message) <= 2000
    assert len(result.model_dump_json()) < 60_000


@pytest.mark.asyncio
async def test_to_verification_result_maps_every_check_one_to_one(repo):
    verifier = ExecutionVerifier(
        gateway(repo),
        [
            FileExistsCheck(path="a.txt"),
            FileContentCheck(path="a.txt", contains=["nope"]),
            NoProhibitedPathsCheck(prohibited=["*.pem"]),
        ],
    )
    await verifier.verify(state_for(repo))

    result = to_verification_result(verifier.last_report)

    assert [c.status for c in result.checks] == ["PASS", "FAIL", "PASS"]
    assert result.status == "FAIL"
    assert all(c.duration_ms is not None for c in result.checks)


def make_runtime(verifier):
    return AgentRuntime(
        understanding_service=TaskUnderstandingService(MockLLMClient()),
        planner=Planner(MockLLMClient()),
        action_generator=ActionGenerator(),
        executor=MockActionExecutor(),
        verifier=verifier,
    )


async def run_task(runtime, repo):
    task = Task(id="task-rt", description="Fix calculator bug", workspace_path=str(repo))
    return await runtime.run(task, RepositoryContext(root=str(repo), summary="demo"))


@pytest.mark.asyncio
async def test_runtime_completes_only_when_the_evidence_passes(repo):
    verifier = ExecutionVerifier(gateway(repo), [FileExistsCheck(path="a.txt")])

    state = await run_task(make_runtime(verifier), repo)

    assert state.status == "COMPLETED"
    assert state.verification_result.status == "PASS"
    assert state.verification_result.checks[0].kind == "file_exists"


@pytest.mark.asyncio
async def test_runtime_does_not_complete_when_the_evidence_fails(repo):
    verifier = ExecutionVerifier(gateway(repo), [FileExistsCheck(path="feature.py")])

    state = await run_task(make_runtime(verifier), repo)

    assert state.status == "FAILED"
    assert state.verification_result.status == "FAIL"
    assert "feature.py does not exist" in state.verification_result.checks[0].message
