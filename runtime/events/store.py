import json
import sqlite3
from pathlib import Path

from runtime.events.event import AgentEvent


class SQLiteEventStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    action_id TEXT,
                    message TEXT NOT NULL,
                    data_json TEXT NOT NULL
                )
                """
            )

    def append(self, event: AgentEvent) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO events (
                    event_id,
                    task_id,
                    event_type,
                    timestamp,
                    action_id,
                    message,
                    data_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.task_id,
                    event.event_type,
                    event.timestamp.isoformat(),
                    event.action_id,
                    event.message,
                    json.dumps(event.data),
                ),
            )

    def get_task_events(self, task_id: str) -> list[AgentEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    event_id,
                    task_id,
                    event_type,
                    timestamp,
                    action_id,
                    message,
                    data_json
                FROM events
                WHERE task_id = ?
                ORDER BY timestamp ASC, rowid ASC
                """,
                (task_id,),
            ).fetchall()

        return [
            AgentEvent(
                event_id=row["event_id"],
                task_id=row["task_id"],
                event_type=row["event_type"],
                timestamp=row["timestamp"],
                action_id=row["action_id"],
                message=row["message"],
                data=json.loads(row["data_json"]),
            )
            for row in rows
        ]

    def count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM events").fetchone()

        return int(row["count"])
