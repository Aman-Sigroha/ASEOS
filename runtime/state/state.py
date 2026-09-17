from typing import Literal

from pydantic import BaseModel, Field

from runtime.schemas.action import Action
from runtime.schemas.execution import ExecutionResult
from runtime.schemas.plan import Plan
from runtime.schemas.repository import RepositoryContext
from runtime.schemas.task import Task
from runtime.schemas.understanding import TaskUnderstanding


AgentStatus = Literal[
    "CREATED",
    "ANALYZING",
    "PLANNING",
    "READY",
    "EXECUTING",
    "VERIFYING",
    "COMPLETED",
    "FAILED",
]


class AgentState(BaseModel):
    task: Task
    status: AgentStatus = "CREATED"
    understanding: TaskUnderstanding | None = None
    repository_context: RepositoryContext | None = None
    plan: Plan | None = None
    actions: list[Action] = Field(default_factory=list)
    execution_results: list[ExecutionResult] = Field(default_factory=list)
    current_plan_results: list[ExecutionResult] = Field(default_factory=list)
    current_action_index: int = Field(default=0, ge=0)
    replan_count: int = Field(default=0, ge=0)
