from typing import Literal

from pydantic import BaseModel, Field


ActionType = Literal[
    "SEARCH",
    "READ",
    "EDIT",
    "RUN_TEST",
    "RUN_COMMAND",
    "GIT_DIFF",
    "LIST_DIR",
    "FILE_INFO",
    "WRITE",
    "VALIDATE_PATCH",
    "APPLY_PATCH",
    "GIT_STATUS",
    "GIT_INFO",
    "GIT_STAGE",
    "GIT_COMMIT",
    "GIT_BRANCH",
    "GIT_DISCARD",
]


class Action(BaseModel):
    """A validated executable action."""

    id: str = Field(min_length=1)
    type: ActionType
    payload: dict = Field(default_factory=dict)
