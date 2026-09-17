import json
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from runtime.llm.errors import APIError


T = TypeVar("T", bound=BaseModel)


def parse_structured_response(
    content: str,
    response_model: type[T],
) -> T:
    """Parse JSON content and validate it against a Pydantic model."""

    try:
        data: Any = json.loads(content)
    except json.JSONDecodeError as exc:
        raise APIError("LLM returned invalid JSON") from exc

    if not isinstance(data, dict):
        raise APIError("LLM structured response must be a JSON object")

    try:
        return response_model.model_validate(data)
    except ValidationError as exc:
        raise APIError("LLM response does not match expected schema") from exc
