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
        raise NotImplementedError(
            "Structured mock responses have not been implemented yet."
        )

    async def generate_with_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> Any:
        raise NotImplementedError("Tool calling has not been implemented yet.")
