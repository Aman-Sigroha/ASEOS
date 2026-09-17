import pytest
from pydantic import ValidationError

from runtime.schemas.action import Action


def test_search_action():
    action = Action(
        id="action-001",
        type="SEARCH",
        payload={
            "query": "timeout",
            "path": "src/",
        },
    )

    assert action.type == "SEARCH"
    assert action.payload["query"] == "timeout"


def test_invalid_action_type():
    with pytest.raises(ValidationError):
        Action(
            id="action-001",
            type="DELETE_DATABASE",
            payload={},
        )
