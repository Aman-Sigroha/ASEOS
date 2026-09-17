from runtime.agent.executor import ActionExecutor
from runtime.planner.actions import ActionGenerator
from runtime.planner.planner import Planner
from runtime.schemas.repository import RepositoryContext
from runtime.schemas.task import Task
from runtime.state.state import AgentState
from runtime.task.understanding import TaskUnderstandingService


class AgentRuntime:
    """Coordinates the basic ASEOS task-processing pipeline."""

    def __init__(
        self,
        understanding_service: TaskUnderstandingService,
        planner: Planner,
        action_generator: ActionGenerator,
        executor: ActionExecutor,
    ) -> None:
        self.understanding_service = understanding_service
        self.planner = planner
        self.action_generator = action_generator
        self.executor = executor

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

        state.status = "ANALYZING"

        state.understanding = await self.understanding_service.understand(task)

        state.status = "PLANNING"

        state.plan = await self.planner.create_plan(
            task=task,
            understanding=state.understanding,
            repository_context=repository_context,
        )

        state.actions = self.action_generator.generate(state.plan)

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
            return state

        state.status = "EXECUTING"

        for index, action in enumerate(state.actions):
            state.current_action_index = index

            result = await self.executor.execute(action)

            state.execution_results.append(result)

            if not result.success:
                state.status = "FAILED"
                return state

        state.current_action_index = len(state.actions)
        state.status = "COMPLETED"

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
