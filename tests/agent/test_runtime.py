import pytest

from runtime.schemas.execution import ExecutionResult
from runtime.schemas.action import Action
from runtime.state.state import AgentState
from runtime.agent.mock_executor import MockActionExecutor
from runtime.agent.runtime import AgentRuntime
from runtime.llm.mock import MockLLMClient
from runtime.planner.actions import ActionGenerator
from runtime.planner.planner import Planner
from runtime.schemas.plan import Plan
from runtime.schemas.repository import RepositoryContext
from runtime.schemas.task import Task
from runtime.schemas.understanding import TaskUnderstanding
from runtime.task.understanding import TaskUnderstandingService


from runtime.agent.executor import ActionExecutor
from runtime.events.event import AgentEvent
from runtime.events.emitter import EventEmitter
from runtime.events.store import SQLiteEventStore
from runtime.decision.decision import Decision
from runtime.decision.engine import DecisionEngine
from runtime.schemas.plan import Plan, PlanStep


class RecordingExecutor(ActionExecutor):
    def __init__(self, results: list[ExecutionResult]) -> None:
        self.results = results
        self.executed_actions: list[Action] = []

    async def execute(
        self,
        action: Action,
    ) -> ExecutionResult:
        self.executed_actions.append(action)

        return self.results[len(self.executed_actions) - 1]


@pytest.mark.asyncio
async def test_agent_runtime_prepare():
    llm = MockLLMClient()

    understanding_service = TaskUnderstandingService(llm)
    planner = Planner(llm)
    action_generator = ActionGenerator()
    executor = MockActionExecutor()

    runtime = AgentRuntime(
        understanding_service=understanding_service,
        planner=planner,
        action_generator=action_generator,
        executor=executor,
    )

    task = Task(
        id="task-001",
        description="Fix the login timeout bug.",
        workspace_path="C:/projects/example",
    )

    repository_context = RepositoryContext(
        root="C:/projects/example",
        summary="Python authentication project",
    )

    state = await runtime.prepare(
        task=task,
        repository_context=repository_context,
    )

    assert state.task == task
    assert state.status == "READY"

    assert isinstance(
        state.understanding,
        TaskUnderstanding,
    )

    assert isinstance(
        state.plan,
        Plan,
    )

    assert len(state.actions) == 2

    assert state.actions[0].type == "SEARCH"
    assert state.actions[1].type == "READ"

    assert state.current_action_index == 0

    assert executor.executed_actions == []


@pytest.mark.asyncio
async def test_agent_runtime_execute_successfully():
    llm = MockLLMClient()

    understanding_service = TaskUnderstandingService(llm)
    planner = Planner(llm)
    action_generator = ActionGenerator()

    executor = RecordingExecutor(
        results=[
            ExecutionResult(
                action_id="step-1",
                success=True,
                stdout="Search completed",
                exit_code=0,
                duration_ms=10,
            ),
            ExecutionResult(
                action_id="step-2",
                success=True,
                stdout="File read",
                exit_code=0,
                duration_ms=5,
            ),
        ]
    )

    runtime = AgentRuntime(
        understanding_service=understanding_service,
        planner=planner,
        action_generator=action_generator,
        executor=executor,
    )

    task = Task(
        id="task-001",
        description="Fix the login timeout bug.",
        workspace_path="C:/projects/example",
    )

    repository_context = RepositoryContext(
        root="C:/projects/example",
        summary="Python authentication project",
    )

    state = await runtime.prepare(
        task=task,
        repository_context=repository_context,
    )

    assert state.status == "READY"

    state = await runtime.execute(state)

    assert state.status == "COMPLETED"

    assert len(state.execution_results) == 2

    assert state.execution_results[0].action_id == "step-1"
    assert state.execution_results[1].action_id == "step-2"

    assert len(executor.executed_actions) == 2
    assert executor.executed_actions[0].id == "step-1"
    assert executor.executed_actions[1].id == "step-2"

    assert state.current_action_index == 2


@pytest.mark.asyncio
async def test_agent_runtime_stops_when_action_fails():
    llm = MockLLMClient()

    understanding_service = TaskUnderstandingService(llm)
    planner = Planner(llm)
    action_generator = ActionGenerator()

    executor = RecordingExecutor(
        results=[
            ExecutionResult(
                action_id="step-1",
                success=False,
                stdout="",
                stderr="Search failed",
                exit_code=1,
                duration_ms=10,
            ),
            ExecutionResult(
                action_id="step-2",
                success=True,
                stdout="This should not run",
                exit_code=0,
                duration_ms=5,
            ),
        ]
    )

    runtime = AgentRuntime(
        understanding_service=understanding_service,
        planner=planner,
        action_generator=action_generator,
        executor=executor,
    )

    task = Task(
        id="task-001",
        description="Fix the login timeout bug.",
        workspace_path="C:/projects/example",
    )

    repository_context = RepositoryContext(
        root="C:/projects/example",
        summary="Python authentication project",
    )

    state = await runtime.prepare(
        task=task,
        repository_context=repository_context,
    )

    state = await runtime.execute(state)

    assert state.status == "FAILED"

    assert len(state.execution_results) == 1

    assert state.execution_results[0].success is False

    assert len(executor.executed_actions) == 1
    assert executor.executed_actions[0].id == "step-1"

    assert state.current_action_index == 0


@pytest.mark.asyncio
async def test_agent_runtime_rejects_execution_before_ready():
    llm = MockLLMClient()

    understanding_service = TaskUnderstandingService(llm)
    planner = Planner(llm)
    action_generator = ActionGenerator()

    executor = RecordingExecutor(results=[])

    runtime = AgentRuntime(
        understanding_service=understanding_service,
        planner=planner,
        action_generator=action_generator,
        executor=executor,
    )

    task = Task(
        id="task-001",
        description="Fix the bug.",
        workspace_path="C:/projects/example",
    )

    state = AgentState(task=task)

    with pytest.raises(
        ValueError,
        match="Agent state must be READY before execution",
    ):
        await runtime.execute(state)

    assert executor.executed_actions == []


@pytest.mark.asyncio
async def test_agent_runtime_run_executes_complete_pipeline():
    llm = MockLLMClient()

    understanding_service = TaskUnderstandingService(llm)
    planner = Planner(llm)
    action_generator = ActionGenerator()

    executor = RecordingExecutor(
        results=[
            ExecutionResult(
                action_id="step-1",
                success=True,
                stdout="Search completed",
                exit_code=0,
            ),
            ExecutionResult(
                action_id="step-2",
                success=True,
                stdout="Read completed",
                exit_code=0,
            ),
        ]
    )

    runtime = AgentRuntime(
        understanding_service=understanding_service,
        planner=planner,
        action_generator=action_generator,
        executor=executor,
    )

    task = Task(
        id="task-001",
        description="Fix the login timeout bug.",
        workspace_path="C:/projects/example",
    )

    repository_context = RepositoryContext(
        root="C:/projects/example",
        summary="Python authentication project",
    )

    state = await runtime.run(
        task=task,
        repository_context=repository_context,
    )

    assert state.status == "COMPLETED"

    assert state.understanding is not None
    assert state.plan is not None

    assert len(state.actions) == 2
    assert len(state.execution_results) == 2
    assert len(executor.executed_actions) == 2


@pytest.mark.asyncio
async def test_agent_runtime_emits_prepare_events():
    llm = MockLLMClient()

    understanding_service = TaskUnderstandingService(llm)
    planner = Planner(llm)
    action_generator = ActionGenerator()
    executor = RecordingExecutor(results=[])

    emitter = EventEmitter()
    events: list[AgentEvent] = []

    emitter.subscribe(events.append)

    runtime = AgentRuntime(
        understanding_service=understanding_service,
        planner=planner,
        action_generator=action_generator,
        executor=executor,
        event_emitter=emitter,
    )

    task = Task(
        id="task-001",
        description="Fix the login timeout bug.",
        workspace_path="C:/projects/example",
    )

    repository_context = RepositoryContext(
        root="C:/projects/example",
        summary="Python authentication project",
    )

    state = await runtime.prepare(
        task=task,
        repository_context=repository_context,
    )

    assert state.status == "READY"

    assert [event.event_type for event in events] == [
        "TASK_STARTED",
        "TASK_UNDERSTANDING_COMPLETED",
        "PLAN_CREATED",
    ]

    assert all(event.task_id == "task-001" for event in events)

    assert events[0].message == "Task started"
    assert events[1].message == "Task understanding completed"
    assert events[2].message == "Plan created"

    assert events[2].data["action_count"] == len(state.actions)


@pytest.mark.asyncio
async def test_agent_runtime_emits_successful_execution_events():
    llm = MockLLMClient()

    understanding_service = TaskUnderstandingService(llm)
    planner = Planner(llm)
    action_generator = ActionGenerator()

    executor = RecordingExecutor(
        results=[
            ExecutionResult(
                action_id="step-1",
                success=True,
                stdout="Search completed",
                exit_code=0,
                duration_ms=10,
            ),
            ExecutionResult(
                action_id="step-2",
                success=True,
                stdout="Read completed",
                exit_code=0,
                duration_ms=5,
            ),
        ]
    )

    emitter = EventEmitter()
    events: list[AgentEvent] = []
    emitter.subscribe(events.append)

    runtime = AgentRuntime(
        understanding_service=understanding_service,
        planner=planner,
        action_generator=action_generator,
        executor=executor,
        event_emitter=emitter,
    )

    task = Task(
        id="task-001",
        description="Fix the login timeout bug.",
        workspace_path="C:/projects/example",
    )

    repository_context = RepositoryContext(
        root="C:/projects/example",
        summary="Python authentication project",
    )

    state = await runtime.prepare(
        task=task,
        repository_context=repository_context,
    )

    events.clear()

    state = await runtime.execute(state)

    assert state.status == "COMPLETED"

    assert [event.event_type for event in events] == [
        "ACTION_STARTED",
        "ACTION_COMPLETED",
        "ACTION_STARTED",
        "ACTION_COMPLETED",
        "TASK_COMPLETED",
    ]

    assert events[0].action_id == "step-1"
    assert events[1].action_id == "step-1"

    assert events[2].action_id == "step-2"
    assert events[3].action_id == "step-2"

    assert events[-1].task_id == "task-001"


@pytest.mark.asyncio
async def test_agent_runtime_emits_failure_events():
    llm = MockLLMClient()

    understanding_service = TaskUnderstandingService(llm)
    planner = Planner(llm)
    action_generator = ActionGenerator()

    executor = RecordingExecutor(
        results=[
            ExecutionResult(
                action_id="step-1",
                success=False,
                stdout="",
                stderr="Search failed",
                exit_code=1,
                duration_ms=10,
            )
        ]
    )

    emitter = EventEmitter()
    events: list[AgentEvent] = []
    emitter.subscribe(events.append)

    runtime = AgentRuntime(
        understanding_service=understanding_service,
        planner=planner,
        action_generator=action_generator,
        executor=executor,
        event_emitter=emitter,
    )

    task = Task(
        id="task-001",
        description="Fix the login timeout bug.",
        workspace_path="C:/projects/example",
    )

    repository_context = RepositoryContext(
        root="C:/projects/example",
        summary="Python authentication project",
    )

    state = await runtime.prepare(
        task=task,
        repository_context=repository_context,
    )

    events.clear()

    state = await runtime.execute(state)

    assert state.status == "FAILED"

    assert [event.event_type for event in events] == [
        "ACTION_STARTED",
        "ACTION_FAILED",
        "TASK_FAILED",
    ]

    assert events[0].action_id == "step-1"
    assert events[1].action_id == "step-1"
    assert events[2].action_id == "step-1"

    assert events[1].data["exit_code"] == 1


@pytest.mark.asyncio
async def test_agent_runtime_emits_unique_event_ids():
    emitter = EventEmitter()
    events = []
    emitter.subscribe(events.append)

    runtime = AgentRuntime(
        understanding_service=TaskUnderstandingService(MockLLMClient()),
        planner=Planner(MockLLMClient()),
        action_generator=ActionGenerator(),
        executor=MockActionExecutor(),
        event_emitter=emitter,
    )

    task = Task(
        id="task-001",
        description="Fix calculator bug",
        workspace_path="/workspace",
    )

    repository_context = RepositoryContext(
        root="/workspace",
        summary="Python calculator project",
    )

    state = await runtime.run(task, repository_context)

    assert state.status == "COMPLETED"

    event_ids = [event.event_id for event in events]

    assert len(event_ids) == len(set(event_ids))


@pytest.mark.asyncio
async def test_agent_runtime_persists_events(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.db")

    runtime = AgentRuntime(
        understanding_service=TaskUnderstandingService(MockLLMClient()),
        planner=Planner(MockLLMClient()),
        action_generator=ActionGenerator(),
        executor=MockActionExecutor(),
        event_store=store,
    )

    task = Task(
        id="task-001",
        description="Fix calculator bug",
        workspace_path="/workspace",
    )

    repository_context = RepositoryContext(
        root="/workspace",
        summary="Python calculator project",
    )

    state = await runtime.run(task, repository_context)

    assert state.status == "COMPLETED"

    events = store.get_task_events(task.id)

    assert len(events) > 0
    assert events[0].event_type == "TASK_STARTED"
    assert events[-1].event_type == "TASK_COMPLETED"
    assert all(event.task_id == task.id for event in events)


@pytest.mark.asyncio
async def test_agent_runtime_uses_decision_engine():
    class RecordingDecisionEngine(DecisionEngine):
        def __init__(self):
            self.calls = 0

        async def decide(self, state):
            self.calls += 1

            if self.calls == 1:
                return Decision(
                    decision_type="EXECUTE_ACTION",
                    action_id="step-1",
                    reason="Execute first action.",
                    confidence=1.0,
                )

            return Decision(
                decision_type="COMPLETE",
                reason="Execution is complete.",
                confidence=1.0,
            )

    decision_engine = RecordingDecisionEngine()

    runtime = AgentRuntime(
        understanding_service=TaskUnderstandingService(MockLLMClient()),
        planner=Planner(MockLLMClient()),
        action_generator=ActionGenerator(),
        executor=MockActionExecutor(),
        decision_engine=decision_engine,
    )

    task = Task(
        id="task-001",
        description="Fix calculator bug",
        workspace_path="/workspace",
    )

    repository_context = RepositoryContext(
        root="/workspace",
        summary="Python calculator project",
    )

    state = await runtime.run(task, repository_context)

    assert state.status == "COMPLETED"
    assert decision_engine.calls == 2
    assert len(state.execution_results) == 1
    assert state.execution_results[0].action_id == "step-1"


class RecordingReplanner:
    def __init__(self):
        self.calls = 0
        self.execution_results = []

    async def replan(
        self,
        task,
        understanding,
        repository_context,
        current_plan,
        execution_results,
    ):
        self.calls += 1
        self.execution_results = execution_results.copy()

        return Plan(
            task_id=task.id,
            goal="Recovered plan",
            steps=[
                PlanStep(
                    id="replan-step-1",
                    description="Apply corrected implementation",
                    action_type="EDIT",
                    parameters={
                        "path": "src/calculator.py",
                    },
                ),
            ],
        )


@pytest.mark.asyncio
async def test_agent_runtime_replans_after_failed_action():
    class RecoveryExecutor(ActionExecutor):
        def __init__(self):
            self.calls = 0
            self.executed_actions = []

        async def execute(self, action):
            self.calls += 1
            self.executed_actions.append(action)

            if self.calls == 1:
                return ExecutionResult(
                    action_id=action.id,
                    success=False,
                    stderr="Initial approach failed",
                    exit_code=1,
                    duration_ms=10,
                )

            return ExecutionResult(
                action_id=action.id,
                success=True,
                exit_code=0,
                duration_ms=10,
            )

    replanner = RecordingReplanner()
    executor = RecoveryExecutor()

    runtime = AgentRuntime(
        understanding_service=TaskUnderstandingService(MockLLMClient()),
        planner=Planner(MockLLMClient()),
        action_generator=ActionGenerator(),
        executor=executor,
        replanner=replanner,
    )

    task = Task(
        id="task-001",
        description="Fix calculator bug",
        workspace_path="/workspace",
    )

    repository_context = RepositoryContext(
        root="/workspace",
        summary="Python calculator project",
    )

    state = await runtime.run(
        task,
        repository_context,
    )

    assert state.status == "COMPLETED"
    assert replanner.calls == 1
    assert state.replan_count == 1

    assert len(replanner.execution_results) == 1
    assert replanner.execution_results[0].success is False

    assert len(state.execution_results) == 2
    assert state.execution_results[0].success is False
    assert state.execution_results[1].success is True

    assert len(state.current_plan_results) == 1
    assert state.current_plan_results[0].success is True

    assert executor.calls == 2
    assert executor.executed_actions[1].id == "replan-step-1"


@pytest.mark.asyncio
async def test_agent_runtime_stops_after_max_replans():
    class AlwaysFailExecutor(ActionExecutor):
        async def execute(self, action):
            return ExecutionResult(
                action_id=action.id,
                success=False,
                stderr="Always fails",
                exit_code=1,
            )

    replanner = RecordingReplanner()

    runtime = AgentRuntime(
        understanding_service=TaskUnderstandingService(MockLLMClient()),
        planner=Planner(MockLLMClient()),
        action_generator=ActionGenerator(),
        executor=AlwaysFailExecutor(),
        replanner=replanner,
        max_replans=2,
    )

    task = Task(
        id="task-001",
        description="Fix calculator bug",
        workspace_path="/workspace",
    )

    repository_context = RepositoryContext(
        root="/workspace",
        summary="Python calculator project",
    )

    state = await runtime.run(
        task,
        repository_context,
    )

    assert state.status == "FAILED"
    assert state.replan_count == 2
    assert replanner.calls == 2
