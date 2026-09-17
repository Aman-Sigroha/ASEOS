from abc import ABC, abstractmethod
from typing import Any


class LLMClient(ABC):
    """Provider-agnostic interface for interacting with an LLM."""

    @abstractmethod
    async def generate(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> str:
        """Generate a plain-text response."""
        raise NotImplementedError

    @abstractmethod
    async def generate_structured(
        self,
        messages: list[dict[str, str]],
        schema: type[Any],
        **kwargs: Any,
    ) -> Any:
        """Generate a response conforming to the supplied schema."""
        raise NotImplementedError
