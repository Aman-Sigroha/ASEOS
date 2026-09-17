from typing import Literal

from pydantic import BaseModel, Field


ActionType = Literal[
    "SEARCH",
    "READ",
    "EDIT",
    "RUN_TEST",
    "RUN_COMMAND",
    "GIT_DIFF",
]


class Action(BaseModel):
    """A validated executable action."""

    id: str = Field(min_length=1)
    type: ActionType
    payload: dict
