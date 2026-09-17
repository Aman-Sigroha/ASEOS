from runtime.llm.base import LLMClient
from runtime.schemas.plan import Plan
from runtime.schemas.repository import RepositoryContext
from runtime.schemas.task import Task
from runtime.schemas.understanding import TaskUnderstanding


class Planner:
    """Creates a structured engineering plan from task context."""

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def create_plan(
        self,
        task: Task,
        understanding: TaskUnderstanding,
        repository_context: RepositoryContext,
    ) -> Plan:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an AI software engineering planner. "
                    "Create a clear, ordered plan for accomplishing "
                    "the user's engineering task. "
                    "Use the task understanding and repository context "
                    "provided. "
                    "Return only structured data matching the requested schema."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Task:\n"
                    f"{task.description}\n\n"
                    f"Goal:\n"
                    f"{understanding.goal}\n\n"
                    f"Expected outcome:\n"
                    f"{understanding.expected_outcome}\n\n"
                    f"Constraints:\n"
                    f"{understanding.constraints}\n\n"
                    f"Verification requirements:\n"
                    f"{understanding.verification_requirements}\n\n"
                    f"Repository:\n"
                    f"{repository_context.summary}\n\n"
                    f"Repository root:\n"
                    f"{repository_context.root}"
                ),
            },
        ]

        return await self.llm.generate_structured(
            messages=messages,
            response_model=Plan,
        )
