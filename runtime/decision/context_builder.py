from runtime.decision.context import DecisionContext
from runtime.state.state import AgentState
from runtime.tools.registry import ToolRegistry


class DecisionContextBuilder:
    def __init__(
        self,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self.tool_registry = tool_registry or ToolRegistry.default()

    def build(self, state: AgentState) -> DecisionContext:
        if state.understanding is None:
            raise ValueError("Agent state does not contain task understanding.")

        if state.plan is None:
            raise ValueError("Agent state does not contain a plan.")

        if state.repository_context is None:
            raise ValueError("Agent state does not contain repository context.")

        return DecisionContext(
            task_id=state.task.id,
            task_description=state.task.description,
            understanding=state.understanding,
            repository_summary=state.repository_context.summary,
            plan_goal=state.plan.goal,
            actions=state.actions.copy(),
            available_tools=self.tool_registry.list_available(),
            current_action_index=state.current_action_index,
            execution_results=state.execution_results.copy(),
            current_plan_results=state.current_plan_results.copy(),
            verification_result=state.verification_result,
            replan_count=state.replan_count,
            status=state.status,
        )
