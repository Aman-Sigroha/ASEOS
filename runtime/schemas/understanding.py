from pydantic import BaseModel, Field


class TaskUnderstanding(BaseModel):
    """Structured interpretation of a user's software engineering task."""

    goal: str = Field(min_length=1)
    expected_outcome: str = Field(min_length=1)
    constraints: list[str] = Field(default_factory=list)
    verification_requirements: list[str] = Field(default_factory=list)
