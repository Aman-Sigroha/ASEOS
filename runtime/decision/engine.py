from abc import ABC, abstractmethod

from runtime.decision.context import DecisionContext
from runtime.decision.decision import Decision


class DecisionEngine(ABC):
    @abstractmethod
    async def decide(self, context: DecisionContext) -> Decision:
        raise NotImplementedError
