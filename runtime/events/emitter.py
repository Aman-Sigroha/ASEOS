from collections.abc import Callable
from typing import Protocol

from runtime.events.event import AgentEvent


EventHandler = Callable[[AgentEvent], None]


class EventStore(Protocol):
    def append(self, event: AgentEvent) -> None: ...


class EventEmitter:
    def __init__(self, event_store: EventStore | None = None) -> None:
        self._handlers: list[EventHandler] = []
        self._event_store = event_store

    def subscribe(self, handler: EventHandler) -> None:
        self._handlers.append(handler)

    def emit(self, event: AgentEvent) -> None:
        if self._event_store is not None:
            self._event_store.append(event)

        for handler in self._handlers:
            handler(event)
