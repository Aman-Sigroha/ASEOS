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
from runtime.planner.replanner import Replanner
from runtime.verification.verifier import Verifier
from runtime.decision.context_builder import DecisionContextBuilder
from runtime.decision.validator import (
    DecisionValidationError,
    DecisionValidator,
)


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
        replanner: Replanner | None = None,
        max_replans: int = 3,
        verifier: Verifier | None = None,
        decision_context_builder: DecisionContextBuilder | None = None,
        decision_validator: DecisionValidator | None = None,
    ) -> None:
        self.understanding_service = understanding_service
        self.planner = planner
        self.action_generator = action_generator
        self.executor = executor
        if max_replans < 0:
            raise ValueError("max_replans cannot be negative.")

        self.replanner = replanner
        self.max_replans = max_replans
        self.decision_engine = decision_engine or DeterministicDecisionEngine()
        self.decision_validator = decision_validator or DecisionValidator()
        if event_emitter is not None and event_store is not None:
            raise ValueError("Provide either event_emitter or event_store, not both.")

        self.decision_context_builder = (
            decision_context_builder or DecisionContextBuilder()
        )

        self.event_emitter = event_emitter or EventEmitter(event_store=event_store)
        self.verifier = verifier

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
            decision_context = self.decision_context_builder.build(state)

            decision = await self.decision_engine.decide(decision_context)

            try:
                self.decision_validator.validate(
                    decision=decision,
                    context=decision_context,
                    max_replans=self.max_replans,
                )
            except DecisionValidationError as exc:
                state.status = "FAILED"

                self._emit(
                    task_id=state.task.id,
                    event_type="TASK_FAILED",
                    message="Decision validation failed.",
                    data={
                        "decision_type": decision.decision_type,
                        "action_id": decision.action_id,
                        "reason": str(exc),
                    },
                )

                return state

            if decision.decision_type == "COMPLETE":
                if self.verifier is None:
                    state.current_action_index = len(state.actions)
                    state.status = "COMPLETED"

                    self._emit(
                        task_id=state.task.id,
                        event_type="TASK_COMPLETED",
                        message="Task completed successfully",
                    )

                    return state

                state.status = "VERIFYING"

                self._emit(
                    task_id=state.task.id,
                    event_type="VERIFICATION_STARTED",
                    message="Task verification started",
                )

                verification_result = await self.verifier.verify(state)
                state.verification_result = verification_result

                if verification_result.status == "PASS":
                    state.current_action_index = len(state.actions)
                    state.status = "COMPLETED"

                    self._emit(
                        task_id=state.task.id,
                        event_type="VERIFICATION_COMPLETED",
                        message="Task verification passed",
                        data={
                            "status": verification_result.status,
                            "check_count": len(verification_result.checks),
                            "summary": verification_result.summary,
                        },
                    )

                    self._emit(
                        task_id=state.task.id,
                        event_type="TASK_COMPLETED",
                        message="Task completed successfully",
                    )

                    return state

                self._emit(
                    task_id=state.task.id,
                    event_type="VERIFICATION_FAILED",
                    message="Task verification failed",
                    data={
                        "status": verification_result.status,
                        "check_count": len(verification_result.checks),
                        "summary": verification_result.summary,
                    },
                )

                if self.replanner is None:
                    state.status = "FAILED"

                    self._emit(
                        task_id=state.task.id,
                        event_type="TASK_FAILED",
                        message=(
                            "Task verification failed and replanning is not configured."
                        ),
                    )

                    return state

                if state.replan_count >= self.max_replans:
                    state.status = "FAILED"

                    self._emit(
                        task_id=state.task.id,
                        event_type="TASK_FAILED",
                        message=(
                            "Maximum replanning attempts reached after verification failure."
                        ),
                        data={
                            "replan_count": state.replan_count,
                            "max_replans": self.max_replans,
                        },
                    )

                    return state

                state.status = "EXECUTING"
                continue

            if decision.decision_type == "REPLAN":
                if self.replanner is None:
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
                            "but replanning is not configured."
                        ),
                    )

                    return state

                if state.replan_count >= self.max_replans:
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
                        message="Maximum replanning attempts reached.",
                        data={
                            "replan_count": state.replan_count,
                            "max_replans": self.max_replans,
                        },
                    )

                    return state

                state.replan_count += 1

                new_plan = await self.replanner.replan(
                    task=state.task,
                    understanding=state.understanding,
                    repository_context=state.repository_context,
                    current_plan=state.plan,
                    execution_results=state.execution_results,
                    verification_result=state.verification_result,
                )

                if new_plan.task_id != state.task.id:
                    state.status = "FAILED"

                    self._emit(
                        task_id=state.task.id,
                        event_type="TASK_FAILED",
                        message="Replanner returned a plan for the wrong task.",
                        data={
                            "expected_task_id": state.task.id,
                            "received_task_id": new_plan.task_id,
                        },
                    )

                    return state

                state.plan = new_plan
                state.actions = self.action_generator.generate(new_plan)

                state.current_action_index = 0
                state.current_plan_results.clear()
                state.verification_result = None

                self._emit(
                    task_id=state.task.id,
                    event_type="PLAN_CREATED",
                    message="Plan replanned successfully",
                    data={
                        "step_count": len(new_plan.steps),
                        "action_count": len(state.actions),
                        "replan_count": state.replan_count,
                        "replanned": True,
                    },
                )

                continue

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
            state.current_plan_results.append(result)

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
