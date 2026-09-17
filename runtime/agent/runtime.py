from runtime.agent.executor import ActionExecutor
from runtime.events.emitter import EventEmitter, EventStore
from runtime.events.event import AgentEvent
from runtime.planner.actions import ActionGenerator
from runtime.planner.planner import Planner
from runtime.schemas.repository import RepositoryContext
from runtime.schemas.task import Task
from runtime.state.state import AgentState
from runtime.task.understanding import TaskUnderstandingService
from uuid import uuid4
from runtime.decision.decision import Decision
from runtime.decision.deterministic import DeterministicDecisionEngine
from runtime.decision.engine import DecisionEngine


class AgentRuntime:
    """Coordinates the basic ASEOS task-processing pipeline."""

    def __init__(
        self,
        understanding_service: TaskUnderstandingService,
        planner: Planner,
        action_generator: ActionGenerator,
        executor: ActionExecutor,
        event_emitter: EventEmitter | None = None,
        event_store: EventStore | None = None,
        decision_engine: DecisionEngine | None = None,
    ) -> None:
        self.understanding_service = understanding_service
        self.planner = planner
        self.action_generator = action_generator
        self.executor = executor
        self.decision_engine = decision_engine or DeterministicDecisionEngine()
        if event_emitter is not None and event_store is not None:
            raise ValueError("Provide either event_emitter or event_store, not both.")

        self.event_emitter = event_emitter or EventEmitter(event_store=event_store)

    def _emit(
        self,
        task_id: str,
        event_type: str,
        message: str = "",
        action_id: str | None = None,
        data: dict | None = None,
    ) -> None:
        """Emit an agent event."""

        event = AgentEvent(
            event_id=str(uuid4()),
            task_id=task_id,
            event_type=event_type,
            action_id=action_id,
            message=message,
            data=data or {},
        )

        self.event_emitter.emit(event)

    async def prepare(
        self,
        task: Task,
        repository_context: RepositoryContext,
    ) -> AgentState:
        """Analyze a task, create a plan, and generate actions."""

        state = AgentState(
            task=task,
            repository_context=repository_context,
        )

        self._emit(
            task_id=task.id,
            event_type="TASK_STARTED",
            message="Task started",
        )

        state.status = "ANALYZING"

        state.understanding = await self.understanding_service.understand(task)

        self._emit(
            task_id=task.id,
            event_type="TASK_UNDERSTANDING_COMPLETED",
            message="Task understanding completed",
        )

        state.status = "PLANNING"

        state.plan = await self.planner.create_plan(
            task=task,
            understanding=state.understanding,
            repository_context=repository_context,
        )

        state.actions = self.action_generator.generate(state.plan)

        self._emit(
            task_id=task.id,
            event_type="PLAN_CREATED",
            message="Plan created",
            data={
                "step_count": len(state.plan.steps),
                "action_count": len(state.actions),
            },
        )

        state.status = "READY"

        return state

    async def execute(self, state: AgentState) -> AgentState:
        if state.status != "READY":
            raise ValueError(
                f"Agent state must be READY before execution; "
                f"current status is {state.status}."
            )

        state.status = "EXECUTING"

        while True:
            decision = await self.decision_engine.decide(state)

            if decision.decision_type == "COMPLETE":
                state.current_action_index = len(state.actions)
                state.status = "COMPLETED"

                self._emit(
                    task_id=state.task.id,
                    event_type="TASK_COMPLETED",
                    message="Task completed successfully",
                )

                return state

            if decision.decision_type == "REPLAN":
                state.status = "FAILED"

                failed_action_id = (
                    state.execution_results[-1].action_id
                    if state.execution_results
                    else None
                )

                self._emit(
                    task_id=state.task.id,
                    event_type="TASK_FAILED",
                    action_id=failed_action_id,
                    message=(
                        "Decision engine requested replanning, "
                        "but replanning is not yet implemented."
                    ),
                )

                return state

            if decision.decision_type == "ABORT":
                state.status = "FAILED"

                self._emit(
                    task_id=state.task.id,
                    event_type="TASK_FAILED",
                    message="Task execution was aborted by the decision engine.",
                )

                return state

            if decision.decision_type == "REQUEST_APPROVAL":
                state.status = "FAILED"

                self._emit(
                    task_id=state.task.id,
                    event_type="TASK_FAILED",
                    message=(
                        "Decision engine requested human approval, "
                        "but approval handling is not yet implemented."
                    ),
                )

                return state

            if decision.decision_type != "EXECUTE_ACTION":
                raise ValueError(f"Unsupported decision type: {decision.decision_type}")

            if decision.action_id is None:
                raise ValueError("EXECUTE_ACTION decision must contain an action_id.")

            action_index = next(
                (
                    index
                    for index, action in enumerate(state.actions)
                    if action.id == decision.action_id
                ),
                None,
            )

            if action_index is None:
                state.status = "FAILED"

                self._emit(
                    task_id=state.task.id,
                    event_type="TASK_FAILED",
                    message=(
                        f"Decision referenced unknown action '{decision.action_id}'."
                    ),
                )

                return state

            action = state.actions[action_index]
            state.current_action_index = action_index

            self._emit(
                task_id=state.task.id,
                event_type="ACTION_STARTED",
                action_id=action.id,
                message=f"Started {action.type}",
                data={"action_type": action.type},
            )

            result = await self.executor.execute(action)
            state.execution_results.append(result)

            if not result.success:
                self._emit(
                    task_id=state.task.id,
                    event_type="ACTION_FAILED",
                    action_id=action.id,
                    message=f"Action {action.type} failed",
                    data={
                        "action_type": action.type,
                        "exit_code": result.exit_code,
                    },
                )

                # Let the decision engine inspect the failed execution
                # on the next loop iteration.
                continue

            self._emit(
                task_id=state.task.id,
                event_type="ACTION_COMPLETED",
                action_id=action.id,
                message=f"Completed {action.type}",
                data={
                    "action_type": action.type,
                    "duration_ms": result.duration_ms,
                },
            )

    async def run(
        self,
        task: Task,
        repository_context: RepositoryContext,
    ) -> AgentState:
        """Prepare and execute a software engineering task."""

        state = await self.prepare(
            task=task,
            repository_context=repository_context,
        )

        return await self.execute(state)
