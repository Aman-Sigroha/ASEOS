from runtime.decision.context import DecisionContext
from runtime.decision.decision import Decision
from runtime.decision.engine import DecisionEngine
from runtime.llm.base import LLMClient
from runtime.llm.router import ModelRouter, RoutingRequest


class LLMDecisionEngine(DecisionEngine):
    """Uses an LLM to decide the next step in the agent loop."""

    def __init__(
        self,
        llm: LLMClient | None = None,
        router: ModelRouter | None = None,
    ) -> None:
        if llm is None and router is None:
            raise ValueError("Either llm or router must be provided.")

        if llm is not None and router is not None:
            raise ValueError("Provide either llm or router, not both.")

        self.llm = llm
        self.router = router

    async def decide(self, context: DecisionContext) -> Decision:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are the decision engine of an autonomous "
                    "software engineering system.\n\n"
                    "Your job is to decide what the agent should do next "
                    "based only on the task, current plan, actions, "
                    "execution evidence, and verification evidence.\n\n"
                    "You may choose exactly one of:\n"
                    "- EXECUTE_ACTION: execute one existing planned action\n"
                    "- REPLAN: create a new plan because the current plan "
                    "cannot safely or successfully continue\n"
                    "- COMPLETE: finish when the task has been successfully "
                    "completed and verified\n"
                    "- REQUEST_APPROVAL: stop and request human approval "
                    "for an action requiring authorization\n"
                    "- ABORT: stop when continuing is unsafe or impossible\n\n"
                    "Never invent an action ID. "
                    "Only select an action from the provided actions.\n"
                    "Do not directly execute commands or modify files.\n"
                    "Return only structured data matching the Decision schema."
                ),
            },
            {
                "role": "user",
                "content": self._build_context(context),
            },
        ]

        llm = self.llm

        if self.router is not None:
            llm = self.router.route(
                RoutingRequest(
                    purpose="DECISION",
                    complexity=self._determine_complexity(context),
                    requires_tools=False,
                )
            )

        if llm is None:
            raise RuntimeError("No LLM client is available.")

        return await llm.generate_structured(
            messages=messages,
            response_model=Decision,
        )

    def _determine_complexity(
        self,
        context: DecisionContext,
    ) -> str:
        if context.verification_result is not None:
            return "HIGH"

        if context.current_plan_results:
            last_result = context.current_plan_results[-1]

            if not last_result.success:
                return "HIGH"

        if len(context.actions) > 5:
            return "HIGH"

        return "MEDIUM"

    def _build_context(self, context: DecisionContext) -> str:
        return (
            f"Task ID:\n{context.task_id}\n\n"
            f"Task:\n{context.task_description}\n\n"
            f"Understanding:\n"
            f"Goal: {context.understanding.goal}\n"
            f"Expected outcome: {context.understanding.expected_outcome}\n"
            f"Constraints: {context.understanding.constraints}\n"
            f"Verification requirements: "
            f"{context.understanding.verification_requirements}\n\n"
            f"Repository summary:\n{context.repository_summary}\n\n"
            f"Plan goal:\n{context.plan_goal}\n\n"
            f"Available actions:\n{context.actions}\n\n"
            f"Current action index:\n{context.current_action_index}\n\n"
            f"Execution results:\n{context.execution_results}\n\n"
            f"Current plan execution results:\n"
            f"{context.current_plan_results}\n\n"
            f"Verification result:\n{context.verification_result}\n\n"
            f"Replan count:\n{context.replan_count}\n\n"
            f"Current status:\n{context.status}"
        )
