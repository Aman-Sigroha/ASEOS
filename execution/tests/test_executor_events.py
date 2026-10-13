from pathlib import Path
from uuid import uuid4

from execution.executor import execute_task
from runtime.events.emitter import EventEmitter


def test_execute_task_emits_completion_event():
    repo_path = str(
        Path("fixtures/repositories/calculator").resolve()
    )
    task_id = f"test-executor-events-{uuid4().hex[:8]}"

    emitter = EventEmitter()
    received = []
    emitter.subscribe(received.append)

    result = execute_task(
        repo_path=repo_path,
        task_id=task_id,
        patch={
            "file": "calculator.py",
            "old": "return a * b",
            "new": "return a / b",
        },
        event_emitter=emitter,
    )

    assert result["status"] == "PASS"

    assert [
        event.event_type for event in received
    ] == [
        "ACTION_COMPLETED",
        "VERIFICATION_STARTED",
        "VERIFICATION_COMPLETED",
    ]

    event = received[-1]
    assert event.task_id == task_id
    assert event.data["status"] == "PASS"

    # The diff belongs to the execution result, not the verification event.
    assert "return a / b" in result["diff"]
