from abc import ABC, abstractmethod

from runtime.decision.decision import Decision
from runtime.state.state import AgentState


class DecisionEngine(ABC):
    @abstractmethod
    async def decide(self, state: AgentState) -> Decision:
        raise NotImplementedError
