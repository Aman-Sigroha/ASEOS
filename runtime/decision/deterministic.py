from runtime.decision.context import DecisionContext
from runtime.decision.decision import Decision
from runtime.decision.engine import DecisionEngine


class DeterministicDecisionEngine(DecisionEngine):
    async def decide(self, context: DecisionContext) -> Decision:
        if context.status == "COMPLETED":
            return Decision(
                decision_type="COMPLETE",
                reason="The task has already completed.",
                confidence=1.0,
            )

        if context.status == "FAILED":
            return Decision(
                decision_type="REPLAN",
                reason="The previous execution failed and requires replanning.",
                confidence=1.0,
            )

        if (
            context.verification_result is not None
            and context.verification_result.status != "PASS"
        ):
            return Decision(
                decision_type="REPLAN",
                reason="Task verification failed and requires replanning.",
                confidence=1.0,
            )

        if not context.actions:
            return Decision(
                decision_type="COMPLETE",
                reason="There are no actions remaining.",
                confidence=1.0,
            )

        if context.current_plan_results:
            last_result = context.current_plan_results[-1]

            if not last_result.success:
                return Decision(
                    decision_type="REPLAN",
                    reason="The most recent action failed.",
                    confidence=1.0,
                )

        next_action_index = len(context.current_plan_results)

        if next_action_index >= len(context.actions):
            return Decision(
                decision_type="COMPLETE",
                reason="All planned actions have been executed successfully.",
                confidence=1.0,
            )

        next_action = context.actions[next_action_index]

        return Decision(
            decision_type="EXECUTE_ACTION",
            action_id=next_action.id,
            reason="The next planned action is ready for execution.",
            confidence=1.0,
        )
