from typing import Literal

from pydantic import BaseModel, Field


DecisionType = Literal[
    "EXECUTE_ACTION",
    "REPLAN",
    "COMPLETE",
    "ABORT",
    "REQUEST_APPROVAL",
]


class Decision(BaseModel):
    decision_type: DecisionType
    action_id: str | None = None
    reason: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
