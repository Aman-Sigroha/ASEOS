from typing import Literal

from pydantic import BaseModel

from runtime.llm.base import LLMClient


RoutingPurpose = Literal[
    "TASK_UNDERSTANDING",
    "PLANNING",
    "DECISION",
    "REPLANNING",
    "GENERAL",
]

Complexity = Literal[
    "LOW",
    "MEDIUM",
    "HIGH",
]


class RoutingRequest(BaseModel):
    purpose: RoutingPurpose
    complexity: Complexity = "MEDIUM"
    requires_tools: bool = False
    prefer_low_latency: bool = False


class ModelRouter:
    """Selects an LLM client using a deterministic routing policy."""

    def __init__(
        self,
        clients: dict[str, LLMClient],
        default_model: str,
    ) -> None:
        if not clients:
            raise ValueError("At least one LLM client must be configured.")

        if default_model not in clients:
            raise ValueError(f"Default model '{default_model}' is not configured.")

        self.clients = clients
        self.default_model = default_model

    def route(self, request: RoutingRequest) -> LLMClient:
        model_name = self._select_model(request)
        return self.clients[model_name]

    def _select_model(self, request: RoutingRequest) -> str:
        # Prefer a low-latency model when explicitly requested.
        if request.prefer_low_latency and "fast" in self.clients:
            return "fast"

        # Tool-capable work can be routed to a dedicated model.
        if request.requires_tools and "tool" in self.clients:
            return "tool"

        # High-complexity engineering reasoning can use a reasoning model.
        if request.complexity == "HIGH" and "reasoning" in self.clients:
            return "reasoning"

        # Planning and replanning generally benefit from stronger reasoning.
        if (
            request.purpose in {"PLANNING", "REPLANNING"}
            and "reasoning" in self.clients
        ):
            return "reasoning"

        return self.default_model
