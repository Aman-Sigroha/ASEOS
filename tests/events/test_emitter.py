from runtime.events.emitter import EventEmitter
from runtime.events.event import AgentEvent
from runtime.events.store import SQLiteEventStore


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


def test_event_emitter_persists_event(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.db")
    emitter = EventEmitter(event_store=store)

    event = AgentEvent(
        event_id="event-001",
        task_id="task-001",
        event_type="TASK_STARTED",
        message="Task started",
    )

    emitter.emit(event)

    stored_events = store.get_task_events("task-001")

    assert len(stored_events) == 1
    assert stored_events[0].event_id == "event-001"


def test_event_emitter_persists_multiple_events(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.db")
    emitter = EventEmitter(event_store=store)

    events = [
        AgentEvent(
            event_id="event-001",
            task_id="task-001",
            event_type="TASK_STARTED",
            message="Task started",
        ),
        AgentEvent(
            event_id="event-002",
            task_id="task-001",
            event_type="PLAN_CREATED",
            message="Plan created",
        ),
        AgentEvent(
            event_id="event-003",
            task_id="task-001",
            event_type="TASK_COMPLETED",
            message="Task completed",
        ),
    ]

    for event in events:
        emitter.emit(event)

    stored_events = store.get_task_events("task-001")

    assert [event.event_id for event in stored_events] == [
        "event-001",
        "event-002",
        "event-003",
    ]


def test_event_emitter_persists_and_notifies_subscribers(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.db")
    emitter = EventEmitter(event_store=store)

    received: list[AgentEvent] = []
    emitter.subscribe(received.append)

    event = AgentEvent(
        event_id="event-001",
        task_id="task-001",
        event_type="TASK_STARTED",
        message="Task started",
    )

    emitter.emit(event)

    assert received == [event]
    assert store.get_task_events("task-001") == [event]
