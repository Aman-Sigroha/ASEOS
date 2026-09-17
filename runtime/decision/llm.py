from runtime.decision.context import DecisionContext
from runtime.decision.decision import Decision
from runtime.decision.engine import DecisionEngine
from runtime.llm.base import LLMClient


class LLMDecisionEngine(DecisionEngine):
    """Uses an LLM to decide the next step in the agent loop."""

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

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

        return await self.llm.generate_structured(
            messages=messages,
            response_model=Decision,
        )

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
