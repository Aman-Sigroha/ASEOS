import pytest

from runtime.llm.mock import MockLLMClient


@pytest.mark.asyncio
async def test_mock_generate():
    client = MockLLMClient()

    result = await client.generate(
        [
            {
                "role": "user",
                "content": "Hello",
            }
        ]
    )

    assert result == "mock response"
