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
