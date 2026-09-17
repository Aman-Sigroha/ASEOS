from typing import Literal

from pydantic import BaseModel, Field

from runtime.decision.context import DecisionContext
from runtime.decision.decision import Decision


PolicyStatus = Literal[
    "ALLOW",
    "REQUEST_APPROVAL",
]


class PolicyResult(BaseModel):
    status: PolicyStatus
    reason: str = Field(min_length=1)


class DecisionPolicy:
    """Applies deterministic autonomy rules to validated decisions."""

    def __init__(
        self,
        min_execution_confidence: float = 0.70,
        high_risk_action_types: set[str] | None = None,
    ) -> None:
        if not 0.0 <= min_execution_confidence <= 1.0:
            raise ValueError("min_execution_confidence must be between 0.0 and 1.0.")

        self.min_execution_confidence = min_execution_confidence

        self.high_risk_action_types = (
            high_risk_action_types
            if high_risk_action_types is not None
            else {"RUN_COMMAND"}
        )

    def evaluate(
        self,
        decision: Decision,
        context: DecisionContext,
    ) -> PolicyResult:
        if decision.decision_type == "EXECUTE_ACTION":
            return self._evaluate_execution(decision, context)

        if decision.decision_type == "REQUEST_APPROVAL":
            return PolicyResult(
                status="REQUEST_APPROVAL",
                reason=decision.reason,
            )

        return PolicyResult(
            status="ALLOW",
            reason="Decision type is permitted by the current policy.",
        )

    def _evaluate_execution(
        self,
        decision: Decision,
        context: DecisionContext,
    ) -> PolicyResult:
        if decision.action_id is None:
            raise ValueError("EXECUTE_ACTION decision must contain an action_id.")

        action = next(
            (action for action in context.actions if action.id == decision.action_id),
            None,
        )

        if action is None:
            raise ValueError(
                f"Decision references unknown action '{decision.action_id}'."
            )

        if action.type in self.high_risk_action_types:
            return PolicyResult(
                status="REQUEST_APPROVAL",
                reason=(
                    f"Action type '{action.type}' requires human approval "
                    "under the current autonomy policy."
                ),
            )

        if decision.confidence < self.min_execution_confidence:
            return PolicyResult(
                status="REQUEST_APPROVAL",
                reason=(
                    f"Decision confidence {decision.confidence:.2f} is below "
                    f"the autonomous execution threshold "
                    f"{self.min_execution_confidence:.2f}."
                ),
            )

        return PolicyResult(
            status="ALLOW",
            reason="Decision satisfies the current autonomy policy.",
        )
