import pytest

from runtime.llm.errors import APIError
from runtime.llm.structured import parse_structured_response
from runtime.schemas.plan import Plan


def test_parse_valid_structured_response():
    content = """
    {
        "task_id": "task-001",
        "goal": "Fix the division bug",
        "steps": [
            {
                "id": "step-1",
                "description": "Find the divide implementation"
            }
        ]
    }
    """

    result = parse_structured_response(
        content,
        Plan,
    )

    assert isinstance(result, Plan)
    assert result.task_id == "task-001"
    assert result.goal == "Fix the division bug"
    assert len(result.steps) == 1


def test_parse_invalid_json():
    content = """
    this is not json
    """

    with pytest.raises(
        APIError,
        match="LLM returned invalid JSON",
    ):
        parse_structured_response(
            content,
            Plan,
        )


def test_parse_json_with_wrong_schema():
    content = """
    {
        "hello": "world"
    }
    """

    with pytest.raises(
        APIError,
        match="LLM response does not match expected schema",
    ):
        parse_structured_response(
            content,
            Plan,
        )


def test_parse_json_array_rejected():
    content = """
    [
        {
            "task_id": "task-001"
        }
    ]
    """

    with pytest.raises(
        APIError,
        match="LLM structured response must be a JSON object",
    ):
        parse_structured_response(
            content,
            Plan,
        )
