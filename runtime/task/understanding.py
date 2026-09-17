from runtime.llm.base import LLMClient
from runtime.schemas.task import Task
from runtime.schemas.understanding import TaskUnderstanding


class TaskUnderstandingService:
    """Converts a user task into structured engineering requirements."""

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def understand(self, task: Task) -> TaskUnderstanding:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an AI software engineering task analyst. "
                    "Analyze the user's engineering task and identify "
                    "the goal, expected outcome, constraints, and "
                    "verification requirements. "
                    "Return only structured data matching the requested schema."
                ),
            },
            {
                "role": "user",
                "content": task.description,
            },
        ]

        return await self.llm.generate_structured(
            messages=messages,
            response_model=TaskUnderstanding,
        )
