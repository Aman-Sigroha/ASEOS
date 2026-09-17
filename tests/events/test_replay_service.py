from runtime.events.event import AgentEvent
from runtime.events.replay_service import EventReplayService
from runtime.events.store import SQLiteEventStore


def test_replay_service_reconstructs_task(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.db")

    store.append(
        AgentEvent(
            event_id="event-1",
            task_id="task-001",
            event_type="TASK_STARTED",
        )
    )

    store.append(
        AgentEvent(
            event_id="event-2",
            task_id="task-001",
            event_type="ACTION_STARTED",
            action_id="action-1",
        )
    )

    store.append(
        AgentEvent(
            event_id="event-3",
            task_id="task-001",
            event_type="ACTION_COMPLETED",
            action_id="action-1",
        )
    )

    store.append(
        AgentEvent(
            event_id="event-4",
            task_id="task-001",
            event_type="TASK_COMPLETED",
        )
    )

    service = EventReplayService(store)

    state = service.replay_task("task-001")

    assert state.task_id == "task-001"
    assert state.status == "COMPLETED"
    assert state.completed_action_ids == ["action-1"]
    assert state.current_action_id is None
    assert state.event_count == 4


def test_replay_service_reconstructs_failed_task(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.db")

    store.append(
        AgentEvent(
            event_id="event-1",
            task_id="task-001",
            event_type="TASK_STARTED",
        )
    )

    store.append(
        AgentEvent(
            event_id="event-2",
            task_id="task-001",
            event_type="ACTION_STARTED",
            action_id="action-1",
        )
    )

    store.append(
        AgentEvent(
            event_id="event-3",
            task_id="task-001",
            event_type="ACTION_FAILED",
            action_id="action-1",
        )
    )

    service = EventReplayService(store)

    state = service.replay_task("task-001")

    assert state.status == "FAILED"
    assert state.failed_action_id == "action-1"
    assert state.current_action_id is None
    assert state.event_count == 3


def test_replay_service_rejects_unknown_task(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.db")

    service = EventReplayService(store)

    try:
        service.replay_task("unknown-task")
        assert False
    except ValueError as exc:
        assert str(exc) == ("No event history found for task 'unknown-task'.")
