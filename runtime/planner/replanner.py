from runtime.llm.base import LLMClient
from runtime.schemas.execution import ExecutionResult
from runtime.schemas.plan import Plan
from runtime.schemas.repository import RepositoryContext
from runtime.schemas.task import Task
from runtime.schemas.understanding import TaskUnderstanding
from runtime.verification.result import VerificationResult


class Replanner:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def replan(
        self,
        task: Task,
        understanding: TaskUnderstanding,
        repository_context: RepositoryContext,
        current_plan: Plan,
        execution_results: list[ExecutionResult],
        verification_result: VerificationResult | None = None,
    ) -> Plan:
        latest_result = execution_results[-1] if execution_results else None

        messages = [
            {
                "role": "system",
                "content": (
                    "You are an AI software engineering replanning agent. "
                    "The current engineering plan could not be completed or "
                    "did not pass verification. Analyze the existing plan, "
                    "execution evidence, and verification evidence, "
                    "identify what went wrong, and create a revised ordered "
                    "plan that attempts to accomplish the original task. "
                    "Avoid repeating the same failed approach when the "
                    "available evidence indicates it is inappropriate. "
                    "Return only structured data matching the requested schema."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Task:\n{task.description}\n\n"
                    f"Goal:\n{understanding.goal}\n\n"
                    f"Expected outcome:\n"
                    f"{understanding.expected_outcome}\n\n"
                    f"Constraints:\n"
                    f"{understanding.constraints}\n\n"
                    f"Verification requirements:\n"
                    f"{understanding.verification_requirements}\n\n"
                    f"Repository:\n"
                    f"{repository_context.summary}\n\n"
                    f"Repository root:\n"
                    f"{repository_context.root}\n\n"
                    f"Current plan:\n"
                    f"{current_plan.model_dump_json()}\n\n"
                    f"Execution history:\n"
                    f"{[result.model_dump() for result in execution_results]}\n\n"
                    f"Latest execution result:\n"
                    f"{latest_result.model_dump_json() if latest_result else 'None'}"
                    f"\n\nVerification result:\n"
                    f"{verification_result.model_dump_json() if verification_result else 'None'}"
                ),
            },
        ]

        return await self.llm.generate_structured(
            messages=messages,
            response_model=Plan,
        )
