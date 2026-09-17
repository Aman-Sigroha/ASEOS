from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from runtime.events.event import AgentEvent


def test_agent_event_creation():
    event = AgentEvent(
        event_id="event-001",
        task_id="task-001",
        event_type="TASK_STARTED",
        message="Task started",
    )

    assert event.event_id == "event-001"
    assert event.task_id == "task-001"
    assert event.event_type == "TASK_STARTED"
    assert event.message == "Task started"
    assert event.action_id is None
    assert event.data == {}

    assert event.timestamp.tzinfo == timezone.utc


def test_action_event():
    event = AgentEvent(
        event_id="event-002",
        task_id="task-001",
        event_type="ACTION_COMPLETED",
        action_id="step-1",
        message="Search completed",
        data={
            "success": True,
            "duration_ms": 100,
        },
    )

    assert event.action_id == "step-1"
    assert event.data["success"] is True
    assert event.data["duration_ms"] == 100


def test_invalid_event_type():
    with pytest.raises(ValidationError):
        AgentEvent(
            event_id="event-001",
            task_id="task-001",
            event_type="SOMETHING_RANDOM",
        )


def test_event_requires_ids():
    with pytest.raises(ValidationError):
        AgentEvent(
            event_id="",
            task_id="task-001",
            event_type="TASK_STARTED",
        )

    with pytest.raises(ValidationError):
        AgentEvent(
            event_id="event-001",
            task_id="",
            event_type="TASK_STARTED",
        )
