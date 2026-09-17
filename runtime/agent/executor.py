from abc import ABC, abstractmethod

from runtime.schemas.action import Action
from runtime.schemas.execution import ExecutionResult


class ActionExecutor(ABC):
    """Interface for executing validated agent actions."""

    @abstractmethod
    async def execute(
        self,
        action: Action,
    ) -> ExecutionResult:
        """Execute an action and return its result."""
        raise NotImplementedError
