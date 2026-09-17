from typing import Any

from pydantic import BaseModel, Field

from runtime.schemas.action import ActionType


class PlanStep(BaseModel):
    """A single structured step in an engineering plan."""

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    action_type: ActionType
    parameters: dict[str, Any] = Field(default_factory=dict)


class Plan(BaseModel):
    """Structured plan generated for a software engineering task."""

    task_id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    steps: list[PlanStep] = Field(default_factory=list)
