from runtime.events.replay import EventReplayer, TaskReplayState
from runtime.events.store import SQLiteEventStore


class EventReplayService:
    def __init__(
        self,
        event_store: SQLiteEventStore,
        replayer: EventReplayer | None = None,
    ) -> None:
        self.event_store = event_store
        self.replayer = replayer or EventReplayer()

    def replay_task(self, task_id: str) -> TaskReplayState:
        events = self.event_store.get_task_events(task_id)

        if not events:
            raise ValueError(f"No event history found for task '{task_id}'.")

        return self.replayer.replay(events)
