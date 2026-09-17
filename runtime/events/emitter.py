from collections.abc import Callable

from runtime.events.event import AgentEvent


EventHandler = Callable[[AgentEvent], None]


class EventEmitter:
    """In-memory event publisher for an agent run."""

    def __init__(self) -> None:
        self._handlers: list[EventHandler] = []

    def subscribe(self, handler: EventHandler) -> None:
        """Register a handler that will receive emitted events."""
        self._handlers.append(handler)

    def emit(self, event: AgentEvent) -> None:
        """Publish an event to all registered handlers."""
        for handler in self._handlers:
            handler(event)
