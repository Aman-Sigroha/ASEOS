from typing import Any

from runtime.llm.base import LLMClient


class MockLLMClient(LLMClient):
    """Deterministic LLM implementation used for local development and tests."""

    async def generate(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> str:
        return "mock response"

    async def generate_structured(
        self,
        messages: list[dict[str, str]],
        response_model: type[Any],
        **kwargs: Any,
    ) -> Any:
        if response_model.__name__ == "Plan":
            return response_model.model_validate(
                {
                    "task_id": "task-001",
                    "goal": "Mock goal",
                    "steps": [
                        {
                            "id": "step-1",
                            "description": "Mock step",
                        }
                    ],
                }
            )

        if response_model.__name__ == "TaskUnderstanding":
            return response_model.model_validate(
                {
                    "goal": "Fix the login timeout bug",
                    "expected_outcome": ("Authentication handles timeout correctly"),
                    "constraints": ["Do not break existing authentication"],
                    "verification_requirements": ["Run authentication tests"],
                }
            )

        raise NotImplementedError(
            f"No mock structured response for {response_model.__name__}"
        )

    async def generate_with_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> Any:
        raise NotImplementedError("Tool calling has not been implemented yet.")
