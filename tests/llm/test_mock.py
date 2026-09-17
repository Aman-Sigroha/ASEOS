import pytest

from runtime.llm.mock import MockLLMClient


@pytest.mark.asyncio
async def test_mock_llm_can_be_instantiated():
    llm = MockLLMClient()

    assert llm is not None


@pytest.mark.asyncio
async def test_mock_llm_generate():
    llm = MockLLMClient()

    result = await llm.generate(
        messages=[
            {
                "role": "user",
                "content": "Hello",
            }
        ]
    )

    assert result == "mock response"


@pytest.mark.asyncio
async def test_mock_llm_structured_not_implemented():
    llm = MockLLMClient()

    with pytest.raises(NotImplementedError):
        await llm.generate_structured(
            messages=[],
            response_model=dict,
        )


@pytest.mark.asyncio
async def test_mock_llm_tools_not_implemented():
    llm = MockLLMClient()

    with pytest.raises(NotImplementedError):
        await llm.generate_with_tools(
            messages=[],
            tools=[],
        )
