"""Phase 12: how the execution layer plugs into the runtime (Role 1) and
tells codebase intelligence (Role 3) what changed.

The Role 3 index below is a small stand-in written for these tests: the real
`intelligence/` package is still empty, so this proves the interface, not an
actual indexer."""

import asyncio
import hashlib
import time
from pathlib import Path

import pytest
from pydantic import BaseModel

from execution.artifacts import ArtifactStore
from execution.gateway import ExecutionGateway
from execution.gateway.capabilities import (
    CAP_GIT_DISCARD,
    CAP_GIT_WRITE,
    CAP_SHELL_RUN,
    CAP_WORKTREE_MANAGE,
    DEFAULT_CAPABILITIES,
)
from execution.gateway.disposition import disposition_of
from execution.gateway.tool import Tool, ToolContext, ToolError, ToolOutput, ToolSpec
from execution.gateway.tools import default_tools
from execution.notify import WorkspaceChange, repo_id_of
from execution.recovery.liveness import identity_of
from execution.security.levels import PROFILES, ExecutionLevel
from execution.tests.crash_helpers import PY_POLICY, make_gateway, run_crashing_child, tools
from execution.tests.gitfixtures import git, init_repo, write
from execution.worktrees.manager import WorktreeManager
from runtime.agent.real_executor import RealActionExecutor
from runtime.agent.runtime import AgentRuntime
from runtime.decision.context_builder import DecisionContextBuilder
from runtime.decision.policy import DecisionPolicy
from runtime.llm.mock import MockLLMClient
from runtime.planner.actions import ActionGenerator
from runtime.planner.planner import Planner
from runtime.schemas.action import Action
from runtime.schemas.plan import Plan, PlanStep
from runtime.schemas.repository import RepositoryContext
from runtime.schemas.task import Task
from runtime.task.understanding import TaskUnderstandingService
from runtime.tools.registry import ToolRegistry

ALL_CAPS = DEFAULT_CAPABILITIES | {
    CAP_GIT_WRITE,
    CAP_GIT_DISCARD,
    CAP_SHELL_RUN,
    CAP_WORKTREE_MANAGE,
}


@pytest.fixture
def repo(tmp_path):
    return init_repo(tmp_path / "repo")


@pytest.fixture
def dirs(tmp_path):
    return tmp_path / "state", tmp_path / "store"


def gateway_for(workspace, dirs, caps=ALL_CAPS, **kw):
    return ExecutionGateway(
        str(workspace),
        tools=default_tools(),
        granted_capabilities=caps,
        artifact_store=ArtifactStore(dirs[1]),
        state_dir=dirs[0],
        **kw,
    )


def run(coro):
    return asyncio.run(coro)


def act(gw, action_type, payload, action_id="a-1"):
    return run(gw.execute(Action(id=action_id, type=action_type, payload=payload)))


class _NoArgs(BaseModel):
    pass


def scripted_tool(action_type, outcomes, *, idempotent=False, read_only=False):
    """A tool that fails or succeeds as scripted, counting its runs."""

    class Scripted(Tool):
        spec = ToolSpec(
            name=f"test.scripted_{action_type.lower()}",
            action_type=action_type,
            description="scripted outcomes",
            args_model=_NoArgs,
            capabilities=frozenset(),
            read_only=read_only,
            idempotent=idempotent,
            level=ExecutionLevel.READ_ONLY if read_only else ExecutionLevel.LOW_RISK_MUTATION,
            timeout_seconds=30,
        )
        runs = 0

        def run(self, context: ToolContext, args):
            index = Scripted.runs
            Scripted.runs += 1
            outcome = outcomes[min(index, len(outcomes) - 1)]

            if outcome is None:
                return ToolOutput(stdout="done")

            category, data = outcome
            raise ToolError(category, f"scripted {category}", data=dict(data))

    return Scripted()


# ------------------------------------------------------------ dispositions


def spec_of(**kw):
    base = dict(
        name="x",
        action_type="EDIT",
        description="d",
        args_model=_NoArgs,
        capabilities=frozenset(),
        read_only=False,
    )
    base.update(kw)
    return ToolSpec(**base)


@pytest.mark.parametrize(
    "category,data,spec,expected",
    [
        ("CANCELLED", {}, spec_of(), "ABORT"),
        ("PERMISSION_DENIED", {}, spec_of(), "ESCALATE"),
        ("INTERNAL_ERROR", {}, spec_of(), "ESCALATE"),
        ("WORKSPACE_UNAVAILABLE", {"not_started": True}, spec_of(), "RETRY"),
        ("TIMEOUT", {"not_started": True}, spec_of(), "RETRY"),
        ("TIMEOUT", {}, spec_of(), "REPLAN"),  # started: its effect is unknown
        ("TIMEOUT", {}, spec_of(idempotent=True), "RETRY"),
        ("WORKSPACE_UNAVAILABLE", {}, spec_of(), "REPLAN"),
        ("PATCH_CONFLICT", {}, spec_of(), "REPLAN"),
        ("TEST_FAILURE", {}, spec_of(), "REPLAN"),
        ("INVALID_ARGUMENTS", {}, spec_of(), "REPLAN"),
        ("RESOURCE_LIMIT", {"not_started": True}, spec_of(), "REPLAN"),
        ("UNKNOWN_TOOL", {}, None, "REPLAN"),
    ],
)
def test_disposition_rules(category, data, spec, expected):
    assert disposition_of(category, data, spec)["action"] == expected


def test_failed_results_carry_a_disposition_and_successes_do_not(repo, dirs):
    gw = gateway_for(repo, dirs)
    ok = act(gw, "READ", {"path": "a.txt"})
    assert ok.success and "disposition" not in ok.data

    stale = act(gw, "APPLY_PATCH", {"patch": "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-nope\n+x\n"})
    assert not stale.success
    assert stale.data["disposition"]["action"] == "REPLAN"

    unknown = run(gw.execute(Action(id="u", type="SEARCH", payload={})))  # invalid args
    assert unknown.data["disposition"]["action"] == "REPLAN"


def test_permission_denied_says_what_to_approve(repo, dirs):
    gw = gateway_for(repo, dirs, caps=DEFAULT_CAPABILITIES)
    denied = act(gw, "GIT_COMMIT", {"message": "m", "paths": ["a.txt"]})

    assert denied.error_category == "PERMISSION_DENIED"
    assert denied.data["missing_capabilities"] == [CAP_GIT_WRITE]
    assert denied.data["approvable"] is True
    assert denied.data["not_started"] is True
    assert denied.data["disposition"]["action"] == "ESCALATE"


def test_profile_ceiling_makes_a_denial_unapprovable(repo, dirs):
    gw = gateway_for(repo, dirs, caps=None, profile="read_only")
    denied = act(gw, "GIT_COMMIT", {"message": "m", "paths": ["a.txt"]})

    assert denied.error_category == "PERMISSION_DENIED"
    assert denied.data["approvable"] is False

    gw.grant({CAP_GIT_WRITE})  # the ceiling still wins
    assert CAP_GIT_WRITE not in gw.granted_capabilities
    assert act(gw, "GIT_COMMIT", {"message": "m", "paths": ["a.txt"]}).error_category == "PERMISSION_DENIED"


def test_grant_after_approval_lets_the_action_run(repo, dirs):
    gw = gateway_for(repo, dirs, caps=DEFAULT_CAPABILITIES)
    write(repo, "a.txt", "one\ntwo\n")
    payload = {"paths": ["a.txt"]}
    assert act(gw, "GIT_STAGE", payload).error_category == "PERMISSION_DENIED"

    gw.grant({CAP_GIT_WRITE})
    assert act(gw, "GIT_STAGE", payload).success

    gw.revoke({CAP_GIT_WRITE})
    assert act(gw, "GIT_STAGE", payload).error_category == "PERMISSION_DENIED"


# ------------------------------------------------------------- approvals


def test_approval_set_follows_what_a_tool_needs(repo, dirs):
    gw = gateway_for(repo, dirs, caps=DEFAULT_CAPABILITIES)
    needs = gw.approval_required_action_types()

    assert {
        "RUN_COMMAND", "GIT_STAGE", "GIT_COMMIT", "GIT_BRANCH", "GIT_DISCARD",
        "WORKTREE_CREATE", "WORKTREE_REMOVE",
    } <= needs
    # ordinary editing, testing and reading stay autonomous
    assert not needs & {
        "READ", "SEARCH", "LIST_DIR", "FILE_INFO", "EDIT", "WRITE", "APPLY_PATCH",
        "VALIDATE_PATCH", "RUN_TEST", "GIT_DIFF", "GIT_STATUS", "GIT_INFO",
        "READ_ARTIFACT", "LIST_ARTIFACTS", "TEST_FRESHNESS",
    }


def test_decision_policy_asks_before_a_risky_action(repo, dirs):
    executor = RealActionExecutor(str(repo), gateway=gateway_for(repo, dirs))
    policy = executor.decision_policy()

    assert policy.high_risk_action_types == executor.gateway.approval_required_action_types()
    assert "GIT_COMMIT" in policy.high_risk_action_types


# -------------------------------------------------------------- registry


def test_registry_is_built_from_the_gateway(repo, dirs):
    executor = RealActionExecutor(str(repo), gateway=gateway_for(repo, dirs, caps=DEFAULT_CAPABILITIES))
    registry = executor.tool_registry()

    from typing import get_args
    from runtime.schemas.action import ActionType

    registered = {t.action_type for t in registry._tools.values()}
    assert registered == set(get_args(ActionType))  # every action type has a tool

    assert registry.is_available("READ") and registry.is_available("APPLY_PATCH")
    assert not registry.is_available("GIT_COMMIT")  # known, but not granted
    assert registry.get("GIT_COMMIT").requires_approval is True
    assert registry.get("GIT_COMMIT").capabilities == [CAP_GIT_WRITE]
    assert registry.get("READ").read_only is True
    assert registry.requiring_approval() == set()  # nothing approval-gated is enabled

    executor.grant({CAP_GIT_WRITE})
    assert executor.tool_registry().is_available("GIT_COMMIT")
    assert "GIT_COMMIT" in executor.tool_registry().requiring_approval()


def test_default_registry_is_unchanged():
    assert {t.action_type for t in ToolRegistry.default().list_available()} == {
        "SEARCH", "READ", "EDIT", "RUN_TEST", "RUN_COMMAND", "GIT_DIFF",
    }


# ----------------------------------------------------------------- retry


def executor_with(repo, dirs, tool, **kw):
    gw = ExecutionGateway(
        str(repo), tools=[tool], granted_capabilities=set(), artifact_store=False,
        state_dir=dirs[0],
    )
    return RealActionExecutor(str(repo), gateway=gw, retry_delays=kw.get("delays", (0, 0)))


def test_not_started_failures_are_retried_until_they_clear(repo, dirs):
    tool = scripted_tool(
        "EDIT",
        [("WORKSPACE_UNAVAILABLE", {"not_started": True})] * 2 + [None],
    )
    result = run(executor_with(repo, dirs, tool).execute(Action(id="e", type="EDIT", payload={})))

    assert result.success
    assert type(tool).runs == 3
    assert result.data["attempts"] == 3


def test_retry_budget_is_bounded_and_the_last_failure_is_returned(repo, dirs):
    tool = scripted_tool("EDIT", [("WORKSPACE_UNAVAILABLE", {"not_started": True})])
    result = run(executor_with(repo, dirs, tool).execute(Action(id="e", type="EDIT", payload={})))

    assert not result.success and result.error_category == "WORKSPACE_UNAVAILABLE"
    assert type(tool).runs == 3  # first try + two retries
    assert result.data["attempts"] == 3
    assert result.data["disposition"]["action"] == "RETRY"


@pytest.mark.parametrize("category", ["PATCH_CONFLICT", "TEST_FAILURE", "INVALID_ARGUMENTS", "CANCELLED"])
def test_rejections_are_not_retried(repo, dirs, category):
    tool = scripted_tool("EDIT", [(category, {})])
    result = run(executor_with(repo, dirs, tool).execute(Action(id="e", type="EDIT", payload={})))

    assert not result.success
    assert type(tool).runs == 1
    assert "attempts" not in result.data


def test_a_started_non_idempotent_action_is_not_repeated_after_a_timeout(repo, dirs):
    tool = scripted_tool("EDIT", [("TIMEOUT", {})])
    result = run(executor_with(repo, dirs, tool).execute(Action(id="e", type="EDIT", payload={})))

    assert type(tool).runs == 1
    assert result.data["disposition"]["action"] == "REPLAN"


def test_an_idempotent_action_is_repeated_after_a_timeout(repo, dirs):
    tool = scripted_tool("READ", [("TIMEOUT", {}), None], idempotent=True, read_only=True)
    result = run(executor_with(repo, dirs, tool).execute(Action(id="r", type="READ", payload={})))

    assert result.success and type(tool).runs == 2


def test_a_held_lease_is_reported_as_retryable_and_never_started(tmp_path):
    repo = init_repo(tmp_path / "repo")
    manager = WorktreeManager(repo)
    record = manager.create("lease-task")
    first = ExecutionGateway(record.path, tools=default_tools(), granted_capabilities=ALL_CAPS,
                             artifact_store=False, owner_id="first")
    second = ExecutionGateway(record.path, tools=default_tools(), granted_capabilities=ALL_CAPS,
                              artifact_store=False, owner_id="second")
    assert act(first, "WRITE", {"path": "x.txt", "content": "1\n"}).success

    blocked = act(second, "WRITE", {"path": "y.txt", "content": "2\n"})
    assert blocked.error_category == "WORKSPACE_UNAVAILABLE"
    assert blocked.data["disposition"]["action"] == "RETRY"
    assert blocked.data["disposition"]["not_started"] is True
    assert not (Path(record.path) / "y.txt").exists()

    executor = RealActionExecutor(record.path, gateway=second, retry_delays=(0,))
    again = run(executor.execute(Action(id="w2", type="WRITE", payload={"path": "y.txt", "content": "2\n"})))
    assert again.data["attempts"] == 2 and again.error_category == "WORKSPACE_UNAVAILABLE"
    first.release_lease()


# --------------------------------------------------------- runtime events


class PlanLLM(MockLLMClient):
    def __init__(self, steps):
        super().__init__()
        self.steps = steps

    async def generate_structured(self, messages, response_model, **kwargs):
        if response_model is Plan:
            return Plan(task_id="role-test", goal="exercise the executor", steps=self.steps)

        return await super().generate_structured(messages=messages, response_model=response_model, **kwargs)


def step(step_id, action_type, **parameters):
    return PlanStep(id=step_id, description=step_id, action_type=action_type, parameters=parameters)


def run_plan(executor, steps, workspace, policy=None, **options):
    events = []
    llm = PlanLLM(steps)
    options = options or executor.runtime_options()

    if policy is not None:
        options["decision_policy"] = policy

    runtime = AgentRuntime(
        understanding_service=TaskUnderstandingService(llm),
        planner=Planner(llm),
        action_generator=ActionGenerator(),
        executor=executor,
        **options,
    )
    runtime.event_emitter.subscribe(events.append)
    task = Task(id="role-test", description="exercise the executor", workspace_path=str(workspace))
    state = asyncio.run(runtime.run(task=task, repository_context=RepositoryContext(root=str(workspace), summary="s")))
    return state, events


def of_type(events, event_type):
    return [e for e in events if e.event_type == event_type]


def test_action_events_carry_execution_evidence(repo, dirs):
    executor = RealActionExecutor(str(repo), gateway=gateway_for(repo, dirs))
    state, events = run_plan(
        executor,
        [step("w1", "WRITE", path="new.txt", content="hi\n"), step("r1", "READ", path="new.txt")],
        repo,
    )

    assert state.status == "COMPLETED"
    first, second = of_type(events, "ACTION_COMPLETED")
    assert first.data["tool"] == "repo.write"
    assert first.data["execution_record"]  # the audit artifact for this action
    assert "execution_record" not in second.data  # reads are not audited by default
    assert first.data["action_type"] == "WRITE"  # the old keys are still there


def test_failed_action_event_says_why_and_what_to_do(repo, dirs):
    executor = RealActionExecutor(str(repo), gateway=gateway_for(repo, dirs, caps=DEFAULT_CAPABILITIES))
    # An approval was given in the registry's eyes (tool listed) but the gateway
    # was never granted the capability: the failure must say what is missing.
    options = executor.runtime_options()
    options["decision_context_builder"] = DecisionContextBuilder(
        tool_registry=ToolRegistry.from_tool_descriptions(
            [{**d, "available": True} for d in executor.gateway.describe_tools()]
        )
    )
    state, events = run_plan(
        executor,
        [step("c1", "GIT_COMMIT", message="m", paths=["a.txt"])],
        repo,
        policy=DecisionPolicy(high_risk_action_types=set()),  # let it reach the gateway
        **options,
    )

    assert state.status == "FAILED"
    (failed,) = of_type(events, "ACTION_FAILED")
    assert failed.data["error_category"] == "PERMISSION_DENIED"
    assert failed.data["disposition"]["action"] == "ESCALATE"
    assert failed.data["missing_capabilities"] == [CAP_GIT_WRITE]
    assert failed.data["approvable"] is True


def test_executor_policy_stops_for_approval_before_execution(repo, dirs):
    executor = RealActionExecutor(str(repo), gateway=gateway_for(repo, dirs))
    state, events = run_plan(
        executor,
        [step("c1", "GIT_STAGE", paths=["a.txt"])],
        repo,
    )

    assert state.status == "FAILED"
    assert not of_type(events, "ACTION_STARTED")  # nothing ran
    (failed,) = of_type(events, "TASK_FAILED")
    assert "requires human approval" in failed.data["reason"]


def test_events_stay_unchanged_for_executors_without_execution_data():
    from runtime.agent.runtime import AgentRuntime as Runtime
    from runtime.schemas.execution import ExecutionResult

    assert Runtime._execution_details(ExecutionResult(action_id="a", success=True)) == {}


# ---------------------------------------------------------------- recovery


def test_runtime_reports_work_an_earlier_run_left_unfinished(repo, dirs):
    (repo.parent / "change.patch").write_text(
        "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1,2 @@\n one\n+two\n"
        "--- a/b.txt\n+++ b/b.txt\n@@ -1 +1,2 @@\n bee\n+buzz\n",
        encoding="utf-8",
    )
    done = run_crashing_child(repo, dirs[0], dirs[1], "patch_partial")
    assert done.returncode == 7, done.stderr

    executor = RealActionExecutor(str(repo), gateway=make_gateway(repo, dirs[0], dirs[1]))
    state, events = run_plan(executor, [step("r1", "READ", path="a.txt")], repo)

    assert state.status == "COMPLETED"  # reporting does not block the new task
    (report,) = of_type(events, "RECOVERY_REPORTED")
    (item,) = report.data["interrupted"]
    assert item["action_id"] == "doomed-1"
    assert item["verdict"] == "partial"
    # reported once the earlier run is acknowledged elsewhere; nothing was changed here
    assert (repo / "a.txt").read_text() == "one\ntwo\n"
    assert (repo / "b.txt").read_text() == "bee\n"


def test_no_recovery_event_when_nothing_is_unfinished(repo, dirs):
    executor = RealActionExecutor(str(repo), gateway=gateway_for(repo, dirs))
    _, events = run_plan(executor, [step("r1", "READ", path="a.txt")], repo)

    assert not of_type(events, "RECOVERY_REPORTED")
    assert [e.event_type for e in events][:2] == ["TASK_STARTED", "TASK_UNDERSTANDING_COMPLETED"]


def test_a_failing_recovery_check_does_not_stop_the_task(repo, dirs):
    class Broken(RealActionExecutor):
        async def recover(self):
            raise RuntimeError("journal unreadable")

    executor = Broken(str(repo), gateway=gateway_for(repo, dirs))
    state, events = run_plan(executor, [step("r1", "READ", path="a.txt")], repo)

    assert state.status == "COMPLETED"
    (report,) = of_type(events, "RECOVERY_REPORTED")
    assert "journal unreadable" in report.data["error"]


# ------------------------------------------------------------ cancellation


def test_cancelling_the_runtime_stops_the_running_process(tmp_path, repo, dirs):
    pid_file = tmp_path / "child.pid"
    script = f"import os,time; open({str(pid_file)!r},'w').write(str(os.getpid())); time.sleep(300)"
    gw = ExecutionGateway(
        str(repo), tools=tools(), granted_capabilities=ALL_CAPS,
        artifact_store=ArtifactStore(dirs[1]), state_dir=dirs[0],
        stop_grace_seconds=15,
    )
    executor = RealActionExecutor(str(repo), gateway=gw)

    async def scenario():
        llm = PlanLLM([step("sleep1", "RUN_COMMAND", command=["python", "-c", script])])
        runtime = AgentRuntime(
            understanding_service=TaskUnderstandingService(llm),
            planner=Planner(llm),
            action_generator=ActionGenerator(),
            executor=executor,
            **{**executor.runtime_options(), "decision_policy": DecisionPolicy(high_risk_action_types=set())},
        )
        task = Task(id="role-test", description="sleep", workspace_path=str(repo))
        running = asyncio.create_task(
            runtime.run(task=task, repository_context=RepositoryContext(root=str(repo), summary="s"))
        )
        deadline = time.monotonic() + 60

        while not pid_file.exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.1)

        assert pid_file.exists(), "the child never started"
        child = int(pid_file.read_text())
        assert identity_of(child) is not None

        running.cancel()

        with pytest.raises(asyncio.CancelledError):
            await running

        return child

    child = asyncio.run(scenario())
    deadline = time.monotonic() + 30

    while identity_of(child) is not None and time.monotonic() < deadline:
        time.sleep(0.2)

    assert identity_of(child) is None, "the child process survived cancellation"
    assert gw.running() == []
    # the journal entry is closed, and the gateway is still usable
    assert gw.operations.list(repo) == []
    assert act(gw, "READ", {"path": "a.txt"}).success


# ----------------------------------------------------------- Role 3 events


def listen(gw):
    seen: list[WorkspaceChange] = []
    gw.add_change_listener(seen.append)
    return seen


def test_identity_is_shared_by_a_checkout_and_its_worktrees(repo, dirs):
    manager = WorktreeManager(repo)
    record = manager.create("ident")
    main = gateway_for(repo, dirs)
    inside = ExecutionGateway(record.path, tools=default_tools(), artifact_store=False)

    assert main.identity()["repo_id"] == manager.repo_id == record.repo_id
    assert inside.identity()["repo_id"] == manager.repo_id
    assert inside.identity()["workspace_id"] != main.identity()["workspace_id"]
    assert repo_id_of(repo.parent) is None  # not a repository


def test_writes_and_patches_publish_exact_paths(repo, dirs):
    gw = gateway_for(repo, dirs)
    seen = listen(gw)

    act(gw, "WRITE", {"path": "new.txt", "content": "hi\n"}, "w1")
    act(gw, "APPLY_PATCH", {"patch": "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1,2 @@\n one\n+two\n"}, "p1")

    assert [(c.kind, c.paths, c.action_id) for c in seen] == [
        ("files_changed", ["new.txt"], "w1"),
        ("files_changed", ["a.txt"], "p1"),
    ]
    assert seen[0].repo_id == gw.identity()["repo_id"]
    assert seen[0].workspace == str(repo)
    assert seen[0].tool == "repo.write"


def test_commit_and_discard_are_published(repo, dirs):
    gw = gateway_for(repo, dirs)
    seen = listen(gw)
    write(repo, "a.txt", "one\nchanged\n")
    act(gw, "GIT_STAGE", {"paths": ["a.txt"]}, "s1")
    assert not seen  # staging changes the index only
    committed = act(gw, "GIT_COMMIT", {"message": "change a", "paths": ["a.txt"]}, "c1")
    assert committed.success

    write(repo, "b.txt", "scribble\n")
    act(gw, "GIT_DISCARD", {"paths": ["b.txt"]}, "d1")

    assert [c.kind for c in seen] == ["committed", "files_changed"]
    assert seen[0].head == git(repo, "rev-parse", "HEAD").strip()
    assert seen[0].paths == ["a.txt"]
    assert seen[1].paths == ["b.txt"]


def test_reads_failures_and_denials_publish_nothing(repo, dirs):
    gw = gateway_for(repo, dirs, caps=DEFAULT_CAPABILITIES)
    seen = listen(gw)

    act(gw, "READ", {"path": "a.txt"})
    act(gw, "SEARCH", {"query": "one"})
    act(gw, "APPLY_PATCH", {"patch": "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-nope\n+x\n"})  # conflict
    act(gw, "WRITE", {"path": "a.txt", "content": "x\n", "expected_sha256": "0" * 64})  # stale
    act(gw, "GIT_COMMIT", {"message": "m", "paths": ["a.txt"]})  # denied
    act(gw, "RUN_COMMAND", {"command": ["python", "-c", "pass"]})  # denied: never started

    assert seen == []


def test_a_program_publishes_unknown_changes_even_when_it_fails(repo, dirs):
    gw = ExecutionGateway(
        str(repo), tools=tools(), granted_capabilities=ALL_CAPS, artifact_store=False, state_dir=dirs[0]
    )
    seen = listen(gw)
    done = act(gw, "RUN_COMMAND", {"command": ["python", "-c", "open('made.txt','w').write('x'); raise SystemExit(3)"]})

    assert not done.success
    assert [(c.kind, c.paths) for c in seen] == [("unknown_changes", [])]


def test_listener_errors_do_not_change_the_result_or_block_other_listeners(repo, dirs):
    gw = gateway_for(repo, dirs)

    def explode(change):
        raise RuntimeError("index is down")

    gw.add_change_listener(explode)
    seen = listen(gw)
    result = act(gw, "WRITE", {"path": "new.txt", "content": "hi\n"})

    assert result.success
    assert len(seen) == 1
    assert (repo / "new.txt").read_text() == "hi\n"


def test_worktree_lifecycle_is_published_with_its_identity(repo, dirs):
    gw = gateway_for(repo, dirs)
    seen = listen(gw)
    created = act(gw, "WORKTREE_CREATE", {"task_id": "wt1"}, "wc")
    assert created.success
    worktree_id = created.data["worktree_id"]
    act(gw, "WORKTREE_REMOVE", {"worktree_id": worktree_id}, "wr")
    again = act(gw, "WORKTREE_REMOVE", {"worktree_id": worktree_id}, "wr2")  # already gone

    assert [c.kind for c in seen] == ["worktree_created", "worktree_removed"]
    assert seen[0].worktree["path"] == created.data["path"]
    assert seen[0].worktree["branch"] == created.data["branch"]
    assert seen[0].worktree["repo_id"] == gw.identity()["repo_id"]
    assert seen[1].worktree["worktree_id"] == worktree_id
    assert again.success


# ----------------------- worktree -> search -> modify -> refresh (stand-in index)


class FakeIndex:
    """What Role 3 will provide, reduced to its contract: per-workspace file
    contents, kept fresh only by the change events it receives."""

    def __init__(self):
        self.files: dict[str, dict[str, str]] = {}
        self.refreshed: list[tuple[str, list[str]]] = []

    def build(self, workspace: Path):
        self.files[str(workspace)] = {
            p.relative_to(workspace).as_posix(): p.read_text(encoding="utf-8")
            for p in Path(workspace).rglob("*")
            if p.is_file() and ".git" not in p.parts
        }

    def search(self, workspace, needle):
        return sorted(p for p, text in self.files[str(workspace)].items() if needle in text)

    def on_change(self, change: WorkspaceChange):
        workspace = Path(change.workspace)

        if change.kind == "unknown_changes":
            self.build(workspace)
            return

        if change.kind == "files_changed":
            for path in change.paths:
                target = workspace / path
                files = self.files[str(workspace)]

                if target.exists():
                    files[path] = target.read_text(encoding="utf-8")
                else:
                    files.pop(path, None)

            self.refreshed.append((str(workspace), change.paths))


def test_worktree_search_modify_refresh_leaves_the_main_checkout_alone(repo, dirs):
    write(repo, "calc.py", "def calculate(a, b):\n    return a * b\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "add calc")
    main_head = git(repo, "rev-parse", "HEAD").strip()

    main_gw = gateway_for(repo, dirs)
    created = act(main_gw, "WORKTREE_CREATE", {"task_id": "idx"})
    assert created.success
    worktree = Path(created.data["path"])

    index = FakeIndex()
    task_gw = ExecutionGateway(
        str(worktree), tools=default_tools(), granted_capabilities=ALL_CAPS,
        artifact_store=False, owner_id="task-idx",
    )
    task_gw.add_change_listener(index.on_change)
    index.build(worktree)

    assert index.search(worktree, "a * b") == ["calc.py"]  # found by symbol/text

    current = hashlib.sha256((worktree / "calc.py").read_bytes()).hexdigest()
    edit = act(
        task_gw,
        "WRITE",
        {
            "path": "calc.py",
            "content": "def calculate(a, b):\n    return a / b\n",
            "expected_sha256": current,
        },
    )
    assert edit.success

    # the index saw the change without a rebuild
    assert index.refreshed == [(str(worktree), ["calc.py"])]
    assert index.search(worktree, "a * b") == []
    assert index.search(worktree, "a / b") == ["calc.py"]
    # the worktree and the main checkout agree with what the index says
    assert (worktree / "calc.py").read_text().endswith("return a / b\n")
    assert (repo / "calc.py").read_text().endswith("return a * b\n")
    assert git(repo, "status", "--porcelain").replace(".aseos_worktrees/", "").strip() == ""
    assert git(repo, "rev-parse", "HEAD").strip() == main_head
    # and the index can be checked against reality at any time
    assert task_gw.workspace_version()["fingerprint"] != main_gw.workspace_version()["fingerprint"]
    task_gw.release_lease()
