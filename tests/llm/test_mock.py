import pytest

from runtime.llm.mock import MockLLMClient
from runtime.schemas.plan import Plan
from runtime.schemas.understanding import TaskUnderstanding


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
async def test_mock_llm_generate_plan():
    llm = MockLLMClient()

    result = await llm.generate_structured(
        messages=[
            {
                "role": "user",
                "content": "Create a plan.",
            }
        ],
        response_model=Plan,
    )

    assert isinstance(result, Plan)
    assert result.task_id == "task-001"
    assert result.goal == "Mock goal"
    assert len(result.steps) == 1


@pytest.mark.asyncio
async def test_mock_llm_generate_task_understanding():
    llm = MockLLMClient()

    result = await llm.generate_structured(
        messages=[
            {
                "role": "user",
                "content": "Understand this task.",
            }
        ],
        response_model=TaskUnderstanding,
    )

    assert isinstance(result, TaskUnderstanding)

    assert result.goal == "Fix the login timeout bug"

    assert result.expected_outcome == "Authentication handles timeout correctly"

    assert result.constraints == ["Do not break existing authentication"]

    assert result.verification_requirements == ["Run authentication tests"]


@pytest.mark.asyncio
async def test_mock_llm_tools_not_implemented():
    llm = MockLLMClient()

    with pytest.raises(NotImplementedError):
        await llm.generate_with_tools(
            messages=[],
            tools=[],
        )
