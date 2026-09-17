from runtime.decision.context import DecisionContext
from runtime.decision.decision import Decision


class DecisionValidationError(ValueError):
    """Raised when a decision is not valid for the current agent state."""


class DecisionValidator:
    """Validates LLM decisions against the current runtime state."""

    def validate(
        self,
        decision: Decision,
        context: DecisionContext,
        max_replans: int = 3,
    ) -> None:
        if max_replans < 0:
            raise ValueError("max_replans cannot be negative.")

        if decision.decision_type == "EXECUTE_ACTION":
            self._validate_execute_action(decision, context)

        elif decision.decision_type == "COMPLETE":
            self._validate_complete(context)

        elif decision.decision_type == "REPLAN":
            self._validate_replan(context, max_replans)

        elif decision.decision_type in {"REQUEST_APPROVAL", "ABORT"}:
            return

        else:
            raise DecisionValidationError(
                f"Unsupported decision type: {decision.decision_type}"
            )

    def _validate_execute_action(
        self,
        decision: Decision,
        context: DecisionContext,
    ) -> None:
        if decision.action_id is None:
            raise DecisionValidationError(
                "EXECUTE_ACTION decision must contain an action_id."
            )

        if context.current_plan_results:
            last_result = context.current_plan_results[-1]

            if not last_result.success:
                raise DecisionValidationError(
                    "Cannot execute another action after a failed action; "
                    "the decision must request replanning or stop."
                )

        next_action_index = len(context.current_plan_results)

        if next_action_index >= len(context.actions):
            raise DecisionValidationError(
                "EXECUTE_ACTION was requested, but no unexecuted actions remain."
            )

        expected_action = context.actions[next_action_index]

        if decision.action_id != expected_action.id:
            raise DecisionValidationError(
                f"EXECUTE_ACTION referenced '{decision.action_id}', "
                f"but the next valid action is '{expected_action.id}'."
            )

    def _validate_complete(self, context: DecisionContext) -> None:
        if len(context.current_plan_results) < len(context.actions):
            raise DecisionValidationError(
                "COMPLETE cannot be accepted while unexecuted actions remain."
            )

        if any(not result.success for result in context.current_plan_results):
            raise DecisionValidationError(
                "COMPLETE cannot be accepted when an action in the current plan failed."
            )

    def _validate_replan(
        self,
        context: DecisionContext,
        max_replans: int,
    ) -> None:
        if context.replan_count >= max_replans:
            raise DecisionValidationError(
                "REPLAN cannot be accepted because the maximum number "
                "of replans has already been reached."
            )
