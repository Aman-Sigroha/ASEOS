from runtime.agent.executor import ActionExecutor
from runtime.events.emitter import EventEmitter
from runtime.events.event import AgentEvent
from runtime.planner.actions import ActionGenerator
from runtime.planner.planner import Planner
from runtime.schemas.repository import RepositoryContext
from runtime.schemas.task import Task
from runtime.state.state import AgentState
from runtime.task.understanding import TaskUnderstandingService
from uuid import uuid4


class AgentRuntime:
    """Coordinates the basic ASEOS task-processing pipeline."""

    def __init__(
        self,
        understanding_service: TaskUnderstandingService,
        planner: Planner,
        action_generator: ActionGenerator,
        executor: ActionExecutor,
        event_emitter: EventEmitter | None = None,
    ) -> None:
        self.understanding_service = understanding_service
        self.planner = planner
        self.action_generator = action_generator
        self.executor = executor
        self.event_emitter = event_emitter or EventEmitter()

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
        """Execute the prepared actions sequentially."""

        if state.status != "READY":
            raise ValueError(
                f"Agent state must be READY before execution; "
                f"current status is {state.status}."
            )

        if not state.actions:
            state.status = "COMPLETED"

            self._emit(
                task_id=state.task.id,
                event_type="TASK_COMPLETED",
                message="Task completed with no actions",
            )

            return state

        state.status = "EXECUTING"

        for index, action in enumerate(state.actions):
            state.current_action_index = index

            self._emit(
                task_id=state.task.id,
                event_type="ACTION_STARTED",
                action_id=action.id,
                message=f"Started {action.type}",
                data={
                    "action_type": action.type,
                },
            )

            result = await self.executor.execute(action)

            state.execution_results.append(result)

            if not result.success:
                state.status = "FAILED"

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

                self._emit(
                    task_id=state.task.id,
                    event_type="TASK_FAILED",
                    action_id=action.id,
                    message="Task failed during execution",
                )

                return state

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

        state.current_action_index = len(state.actions)
        state.status = "COMPLETED"

        self._emit(
            task_id=state.task.id,
            event_type="TASK_COMPLETED",
            message="Task completed successfully",
        )

        return state

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
