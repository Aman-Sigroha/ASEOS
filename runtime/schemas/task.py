from pydantic import BaseModel, Field


class Task(BaseModel):
    """A user-provided software engineering task."""

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    workspace_path: str = Field(min_length=1)
