from pydantic import BaseModel, Field

from runtime.schemas.action import Action
from runtime.schemas.execution import ExecutionResult
from runtime.schemas.understanding import TaskUnderstanding
from runtime.verification.result import VerificationResult
from runtime.tools.registry import ToolDefinition


class DecisionContext(BaseModel):
    task_id: str = Field(min_length=1)
    task_description: str = Field(min_length=1)

    understanding: TaskUnderstanding

    repository_summary: str = ""

    plan_goal: str = Field(min_length=1)
    actions: list[Action] = Field(default_factory=list)
    available_tools: list[ToolDefinition] = Field(default_factory=list)

    current_action_index: int = Field(default=0, ge=0)

    execution_results: list[ExecutionResult] = Field(default_factory=list)
    current_plan_results: list[ExecutionResult] = Field(default_factory=list)

    verification_result: VerificationResult | None = None

    replan_count: int = Field(default=0, ge=0)

    status: str = Field(min_length=1)
