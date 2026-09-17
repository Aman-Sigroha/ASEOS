from abc import ABC, abstractmethod

from runtime.state.state import AgentState
from runtime.verification.result import VerificationResult


class Verifier(ABC):
    @abstractmethod
    async def verify(self, state: AgentState) -> VerificationResult:
        raise NotImplementedError
