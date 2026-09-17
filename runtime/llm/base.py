from abc import ABC, abstractmethod
from typing import Any


class LLMClient(ABC):
    @abstractmethod
    async def generate(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> str:
        """Generate a text response."""
        raise NotImplementedError

    @abstractmethod
    async def generate_structured(
        self,
        messages: list[dict[str, str]],
        response_model: type,
        **kwargs: Any,
    ):
        """Generate a response validated against a schema."""
        raise NotImplementedError

    @abstractmethod
    async def generate_with_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ):
        """Generate a response with tool-calling support."""
        raise NotImplementedError
