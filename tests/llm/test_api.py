import httpx
import pytest

from runtime.llm.api import APIError, APILLMClient


@pytest.mark.asyncio
async def test_generate_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/chat/completions"

        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Hello from the mock API"}}]},
        )

    transport = httpx.MockTransport(handler)

    llm = APILLMClient(
        base_url="https://example.com",
        api_key="test-key",
        model="test-model",
        transport=transport,
    )

    result = await llm.generate(
        messages=[
            {
                "role": "user",
                "content": "Hello",
            }
        ]
    )

    assert result == "Hello from the mock API"


@pytest.mark.asyncio
async def test_generate_raises_api_error_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"error": {"message": "Internal server error"}},
        )

    transport = httpx.MockTransport(handler)

    llm = APILLMClient(
        base_url="https://example.com",
        api_key="test-key",
        model="test-model",
        transport=transport,
    )

    with pytest.raises(APIError, match="LLM API request failed"):
        await llm.generate(
            messages=[
                {
                    "role": "user",
                    "content": "Hello",
                }
            ]
        )


@pytest.mark.asyncio
async def test_generate_raises_api_error_on_invalid_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"unexpected": "response"},
        )

    transport = httpx.MockTransport(handler)

    llm = APILLMClient(
        base_url="https://example.com",
        api_key="test-key",
        model="test-model",
        transport=transport,
    )

    with pytest.raises(APIError, match="Unexpected LLM API response format"):
        await llm.generate(
            messages=[
                {
                    "role": "user",
                    "content": "Hello",
                }
            ]
        )


@pytest.mark.asyncio
async def test_generate_raises_api_error_on_invalid_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"unexpected": "response"},
        )

    transport = httpx.MockTransport(handler)

    llm = APILLMClient(
        base_url="https://example.com",
        api_key="test-key",
        model="test-model",
        transport=transport,
    )

    with pytest.raises(APIError, match="Unexpected LLM API response format"):
        await llm.generate(
            messages=[
                {
                    "role": "user",
                    "content": "Hello",
                }
            ]
        )
