from runtime.events.emitter import EventEmitter
from runtime.events.event import AgentEvent


def test_event_emitter_calls_subscriber():
    emitter = EventEmitter()
    received: list[AgentEvent] = []

    def handler(event: AgentEvent) -> None:
        received.append(event)

    emitter.subscribe(handler)

    event = AgentEvent(
        event_id="event-001",
        task_id="task-001",
        event_type="TASK_STARTED",
        message="Task started",
    )

    emitter.emit(event)

    assert received == [event]


def test_event_emitter_calls_multiple_subscribers():
    emitter = EventEmitter()

    first: list[AgentEvent] = []
    second: list[AgentEvent] = []

    emitter.subscribe(first.append)
    emitter.subscribe(second.append)

    event = AgentEvent(
        event_id="event-001",
        task_id="task-001",
        event_type="TASK_COMPLETED",
        message="Task completed",
    )

    emitter.emit(event)

    assert first == [event]
    assert second == [event]


def test_event_emitter_with_no_subscribers_does_nothing():
    emitter = EventEmitter()

    event = AgentEvent(
        event_id="event-001",
        task_id="task-001",
        event_type="TASK_STARTED",
    )

    emitter.emit(event)
