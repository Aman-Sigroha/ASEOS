from typing import Any

import httpx

from runtime.llm.base import LLMClient

from runtime.llm.structured import parse_structured_response
from runtime.llm.errors import APIError


class APILLMClient(LLMClient):
    """HTTP-based implementation of the provider-agnostic LLM interface."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.transport = transport

    async def generate(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            **kwargs,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )

                response.raise_for_status()

        except httpx.HTTPError as exc:
            raise APIError(f"LLM API request failed: {exc}") from exc

        data = response.json()

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise APIError("Unexpected LLM API response format") from exc

        if not isinstance(content, str):
            raise APIError("LLM API response content must be a string")

        return content

    async def generate_structured(
        self,
        messages: list[dict[str, str]],
        response_model: type[Any],
        **kwargs: Any,
    ) -> Any:
        content = await self.generate(
            messages=messages,
            **kwargs,
        )

        return parse_structured_response(
            content,
            response_model,
        )

    async def generate_with_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> Any:
        raise NotImplementedError("Tool calling has not been implemented yet.")
