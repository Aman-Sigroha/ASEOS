from datetime import datetime, timezone

from runtime.events.event import AgentEvent
from runtime.events.store import SQLiteEventStore


def make_event(
    event_id: str,
    task_id: str = "task-001",
    event_type: str = "TASK_STARTED",
) -> AgentEvent:
    return AgentEvent(
        event_id=event_id,
        task_id=task_id,
        event_type=event_type,
        timestamp=datetime.now(timezone.utc),
        action_id=None,
        message="Test event",
        data={"key": "value"},
    )


def test_event_store_creates_database(tmp_path):
    db_path = tmp_path / "events.db"

    store = SQLiteEventStore(db_path)

    assert db_path.exists()
    assert store.count() == 0


def test_event_store_appends_event(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.db")
    event = make_event("event-001")

    store.append(event)

    assert store.count() == 1


def test_event_store_reads_task_events(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.db")
    event = make_event("event-001")

    store.append(event)

    events = store.get_task_events("task-001")

    assert len(events) == 1
    assert events[0].event_id == "event-001"
    assert events[0].task_id == "task-001"
    assert events[0].event_type == "TASK_STARTED"
    assert events[0].message == "Test event"
    assert events[0].data == {"key": "value"}


def test_event_store_filters_by_task(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.db")

    store.append(make_event("event-001", task_id="task-001"))
    store.append(make_event("event-002", task_id="task-002"))

    events = store.get_task_events("task-001")

    assert len(events) == 1
    assert events[0].event_id == "event-001"


def test_event_store_preserves_multiple_events(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.db")

    store.append(make_event("event-001", event_type="TASK_STARTED"))
    store.append(make_event("event-002", event_type="PLAN_CREATED"))
    store.append(make_event("event-003", event_type="TASK_COMPLETED"))

    events = store.get_task_events("task-001")

    assert [event.event_id for event in events] == [
        "event-001",
        "event-002",
        "event-003",
    ]


def test_event_store_persists_across_instances(tmp_path):
    db_path = tmp_path / "events.db"

    store = SQLiteEventStore(db_path)

    store.append(make_event("event-001"))

    new_store = SQLiteEventStore(db_path)

    events = new_store.get_task_events("task-001")

    assert len(events) == 1
    assert events[0].event_id == "event-001"
