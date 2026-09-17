from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    """A single step in an engineering plan."""

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)


class Plan(BaseModel):
    """Structured plan generated for a software engineering task."""

    task_id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    steps: list[PlanStep] = Field(default_factory=list)
