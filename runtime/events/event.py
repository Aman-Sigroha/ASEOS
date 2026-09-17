from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


EventType = Literal[
    "TASK_STARTED",
    "TASK_UNDERSTANDING_COMPLETED",
    "PLAN_CREATED",
    "ACTION_STARTED",
    "ACTION_COMPLETED",
    "ACTION_FAILED",
    "VERIFICATION_STARTED",
    "VERIFICATION_COMPLETED",
    "VERIFICATION_FAILED",
    "TASK_COMPLETED",
    "TASK_FAILED",
]


class AgentEvent(BaseModel):
    """Immutable-style record describing an event in an agent run."""

    event_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    event_type: EventType

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    action_id: str | None = None
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
