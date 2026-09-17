from runtime.events.event import AgentEvent
from runtime.events.replay import EventReplayer


def make_event(
    event_id: str,
    event_type: str,
    action_id: str | None = None,
) -> AgentEvent:
    return AgentEvent(
        event_id=event_id,
        task_id="task-001",
        event_type=event_type,
        action_id=action_id,
    )


def test_replay_task_start():

    events = [
        make_event("event-1", "TASK_STARTED"),
    ]

    state = EventReplayer().replay(events)

    assert state.task_id == "task-001"
    assert state.status == "RUNNING"
    assert state.event_count == 1
    assert state.last_event_type == "TASK_STARTED"


def test_replay_successful_action():

    events = [
        make_event("event-1", "TASK_STARTED"),
        make_event("event-2", "ACTION_STARTED", "action-1"),
        make_event("event-3", "ACTION_COMPLETED", "action-1"),
        make_event("event-4", "TASK_COMPLETED"),
    ]

    state = EventReplayer().replay(events)

    assert state.status == "COMPLETED"
    assert state.current_action_id is None
    assert state.completed_action_ids == ["action-1"]
    assert state.failed_action_id is None
    assert state.event_count == 4
    assert state.last_event_type == "TASK_COMPLETED"


def test_replay_failed_action():

    events = [
        make_event("event-1", "TASK_STARTED"),
        make_event("event-2", "ACTION_STARTED", "action-1"),
        make_event("event-3", "ACTION_FAILED", "action-1"),
        make_event("event-4", "TASK_FAILED", "action-1"),
    ]

    state = EventReplayer().replay(events)

    assert state.status == "FAILED"
    assert state.current_action_id is None
    assert state.completed_action_ids == []
    assert state.failed_action_id == "action-1"
    assert state.event_count == 4


def test_replay_multiple_actions():

    events = [
        make_event("event-1", "TASK_STARTED"),
        make_event("event-2", "ACTION_STARTED", "action-1"),
        make_event("event-3", "ACTION_COMPLETED", "action-1"),
        make_event("event-4", "ACTION_STARTED", "action-2"),
        make_event("event-5", "ACTION_COMPLETED", "action-2"),
        make_event("event-6", "TASK_COMPLETED"),
    ]

    state = EventReplayer().replay(events)

    assert state.status == "COMPLETED"
    assert state.completed_action_ids == [
        "action-1",
        "action-2",
    ]
    assert state.current_action_id is None


def test_replay_rejects_empty_history():

    try:
        EventReplayer().replay([])
        assert False
    except ValueError as exc:
        assert str(exc) == "Cannot replay an empty event history."


def test_replay_rejects_mixed_tasks():

    event_1 = make_event("event-1", "TASK_STARTED")

    event_2 = AgentEvent(
        event_id="event-2",
        task_id="task-002",
        event_type="TASK_COMPLETED",
    )

    try:
        EventReplayer().replay([event_1, event_2])
        assert False
    except ValueError as exc:
        assert str(exc) == "All events must belong to the same task."


def test_replay_verification_success():

    events = [
        make_event("event-1", "TASK_STARTED"),
        make_event("event-2", "VERIFICATION_STARTED"),
        AgentEvent(
            event_id="event-3",
            task_id="task-001",
            event_type="VERIFICATION_COMPLETED",
            data={
                "status": "PASS",
                "check_count": 2,
                "summary": "All checks passed.",
            },
        ),
        make_event("event-4", "TASK_COMPLETED"),
    ]

    state = EventReplayer().replay(events)

    assert state.status == "COMPLETED"
    assert state.verification_status == "PASS"
    assert state.verification_summary == "All checks passed."
    assert state.verification_check_count == 2
    assert state.verification_attempts == 1


def test_replay_verification_failure():

    events = [
        make_event("event-1", "TASK_STARTED"),
        make_event("event-2", "VERIFICATION_STARTED"),
        AgentEvent(
            event_id="event-3",
            task_id="task-001",
            event_type="VERIFICATION_FAILED",
            data={
                "status": "FAIL",
                "check_count": 3,
                "summary": "One test failed.",
            },
        ),
    ]

    state = EventReplayer().replay(events)

    assert state.status == "RUNNING"
    assert state.verification_status == "FAIL"
    assert state.verification_summary == "One test failed."
    assert state.verification_check_count == 3
    assert state.verification_attempts == 1


def test_replay_verification_replanning_cycle():

    events = [
        make_event("event-1", "TASK_STARTED"),
        make_event("event-2", "ACTION_STARTED", "action-1"),
        make_event("event-3", "ACTION_COMPLETED", "action-1"),
        make_event("event-4", "VERIFICATION_STARTED"),
        AgentEvent(
            event_id="event-5",
            task_id="task-001",
            event_type="VERIFICATION_FAILED",
            data={
                "status": "FAIL",
                "check_count": 1,
                "summary": "Unit test failed.",
            },
        ),
        AgentEvent(
            event_id="event-6",
            task_id="task-001",
            event_type="PLAN_CREATED",
            data={
                "replanned": True,
                "replan_count": 1,
                "step_count": 1,
                "action_count": 1,
            },
        ),
        make_event("event-7", "ACTION_STARTED", "replan-action-1"),
        make_event("event-8", "ACTION_COMPLETED", "replan-action-1"),
        make_event("event-9", "VERIFICATION_STARTED"),
        AgentEvent(
            event_id="event-10",
            task_id="task-001",
            event_type="VERIFICATION_COMPLETED",
            data={
                "status": "PASS",
                "check_count": 1,
                "summary": "Verification passed.",
            },
        ),
        make_event("event-11", "TASK_COMPLETED"),
    ]

    state = EventReplayer().replay(events)

    assert state.status == "COMPLETED"
    assert state.completed_action_ids == [
        "action-1",
        "replan-action-1",
    ]
    assert state.verification_status == "PASS"
    assert state.verification_summary == "Verification passed."
    assert state.verification_check_count == 1
    assert state.verification_attempts == 2
    assert state.event_count == 11
