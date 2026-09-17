from runtime.decision.decision import Decision
from runtime.decision.engine import DecisionEngine
from runtime.state.state import AgentState


class DeterministicDecisionEngine(DecisionEngine):
    async def decide(self, state: AgentState) -> Decision:
        if state.status == "COMPLETED":
            return Decision(
                decision_type="COMPLETE",
                reason="The task has already completed.",
                confidence=1.0,
            )

        if state.status == "FAILED":
            return Decision(
                decision_type="REPLAN",
                reason="The previous execution failed and requires replanning.",
                confidence=1.0,
            )

        if not state.actions:
            return Decision(
                decision_type="COMPLETE",
                reason="There are no actions remaining.",
                confidence=1.0,
            )

        if state.execution_results:
            last_result = state.execution_results[-1]

            if not last_result.success:
                return Decision(
                    decision_type="REPLAN",
                    reason="The most recent action failed.",
                    confidence=1.0,
                )

        next_action_index = len(state.execution_results)

        if next_action_index >= len(state.actions):
            return Decision(
                decision_type="COMPLETE",
                reason="All planned actions have been executed successfully.",
                confidence=1.0,
            )

        next_action = state.actions[next_action_index]

        return Decision(
            decision_type="EXECUTE_ACTION",
            action_id=next_action.id,
            reason="The next planned action is ready for execution.",
            confidence=1.0,
        )
