from runtime.schemas.action import Action
from runtime.schemas.plan import Plan


class ActionGenerator:
    """Converts structured plan steps into executable actions."""

    def generate(self, plan: Plan) -> list[Action]:
        return [
            Action(
                id=step.id,
                type=step.action_type,
                payload=step.parameters,
            )
            for step in plan.steps
        ]
