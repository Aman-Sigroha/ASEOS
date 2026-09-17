from typing import Literal

from pydantic import BaseModel, Field

from runtime.events.event import AgentEvent


ReplayStatus = Literal[
    "NOT_STARTED",
    "RUNNING",
    "COMPLETED",
    "FAILED",
]


class TaskReplayState(BaseModel):
    task_id: str = Field(min_length=1)
    status: ReplayStatus = "NOT_STARTED"
    current_action_id: str | None = None
    completed_action_ids: list[str] = Field(default_factory=list)
    failed_action_id: str | None = None
    event_count: int = Field(default=0, ge=0)
    last_event_type: str | None = None


class EventReplayer:
    def replay(self, events: list[AgentEvent]) -> TaskReplayState:
        if not events:
            raise ValueError("Cannot replay an empty event history.")

        task_id = events[0].task_id

        if any(event.task_id != task_id for event in events):
            raise ValueError("All events must belong to the same task.")

        state = TaskReplayState(task_id=task_id)

        for event in events:
            state.event_count += 1
            state.last_event_type = event.event_type

            if event.event_type == "TASK_STARTED":
                state.status = "RUNNING"

            elif event.event_type == "ACTION_STARTED":
                state.status = "RUNNING"
                state.current_action_id = event.action_id

            elif event.event_type == "ACTION_COMPLETED":
                if event.action_id is not None:
                    state.completed_action_ids.append(event.action_id)

                    if state.current_action_id == event.action_id:
                        state.current_action_id = None

            elif event.event_type == "ACTION_FAILED":
                state.status = "FAILED"
                state.failed_action_id = event.action_id
                state.current_action_id = None

            elif event.event_type == "TASK_COMPLETED":
                state.status = "COMPLETED"
                state.current_action_id = None

            elif event.event_type == "TASK_FAILED":
                state.status = "FAILED"
                state.current_action_id = None

        return state
