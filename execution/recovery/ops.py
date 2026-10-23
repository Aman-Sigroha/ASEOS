"""The operations journal: what is running, and what was interrupted.

Before a state-changing action starts, its *intent* is written as a small
JSON file (`ops/<workspace id>/<op id>.json`) linking the runtime's
`action_id` to the owner process, the workspace, the Git HEAD and, as the
tool progresses, checkpoints (files with their before/after hashes, the
child process identity). When the action ends the file is removed; the
durable history is the execution record (Phase 10).

A file that is still present after its owner died is an *interrupted*
operation. `reconcile` (see reconcile.py) inspects the real file-system and
process state against the checkpoints instead of trusting or re-running it.
"""

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from execution.artifacts.store import workspace_id
from execution.patches.fileio import atomic_write_bytes
from execution.recovery.liveness import identity_of
from execution.recovery.locations import ops_dir, this_host

MAX_CHECKPOINTS = 20
MAX_FILES_PER_CHECKPOINT = 200


class JournalError(Exception):
    pass


class OperationRecord(BaseModel):
    op_id: str
    action_id: str
    action_type: str
    tool: str
    workspace_id: str
    workspace: str
    task_id: str | None = None
    owner_id: str
    pid: int
    pid_created: int | None = None
    host: str
    state: str = "running"  # running | interrupted
    started_at: float
    updated_at: float
    args_sha256: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    head_before: str | None = None
    checkpoints: list[dict[str, Any]] = Field(default_factory=list)
    process: dict[str, Any] | None = None
    assessment: dict[str, Any] | None = None


class OperationHandle:
    """What a running tool uses to record progress. Never raises."""

    enabled = True

    def __init__(self, log: "OperationsLog", record: OperationRecord, path: Path) -> None:
        self._log = log
        self.record = record
        self.path = path
        self.errors: list[str] = []

    def _save(self) -> None:
        self.record.updated_at = time.time()

        try:
            atomic_write_bytes(self.path, self.record.model_dump_json().encode())
        except OSError as exc:
            self.errors.append(f"journal write failed: {exc}")

    def checkpoint(self, kind: str, **data: Any) -> None:
        if len(self.record.checkpoints) >= MAX_CHECKPOINTS:
            return

        if isinstance(data.get("files"), list):
            data["files"] = data["files"][:MAX_FILES_PER_CHECKPOINT]

        self.record.checkpoints.append({"kind": kind, "at": time.time(), **data})
        self._save()

    def set_process(self, pid: int, created: int | None, run_id: str) -> None:
        self.record.process = {"pid": pid, "created": created, "run_id": run_id}
        self._save()

    def finish(self) -> None:
        """The action ended (any outcome): drop the intent file."""

        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            self.errors.append(f"journal cleanup failed: {exc}")


class NullOperation:
    """Used when the journal is off or for read-only actions."""

    enabled = False
    errors: list[str] = []

    def checkpoint(self, kind: str, **data: Any) -> None:
        return None

    def set_process(self, *args: Any, **kwargs: Any) -> None:
        return None

    def finish(self) -> None:
        return None


class OperationsLog:
    def __init__(self, state_dir: Path) -> None:
        self.root = ops_dir(state_dir)

    def _dir(self, workspace: Path | str) -> Path:
        return self.root / workspace_id(workspace)

    def begin(
        self,
        *,
        action_id: str,
        action_type: str,
        tool: str,
        workspace: Path,
        owner_id: str,
        task_id: str | None,
        args_sha256: str,
        args: dict[str, Any],
        head_before: str | None,
    ) -> OperationHandle:
        me = identity_of(os.getpid())
        now = time.time()
        record = OperationRecord(
            op_id=f"op-{uuid.uuid4().hex[:12]}",
            action_id=action_id,
            action_type=action_type,
            tool=tool,
            workspace_id=workspace_id(workspace),
            workspace=str(workspace),
            task_id=task_id,
            owner_id=owner_id,
            pid=os.getpid(),
            pid_created=me.created if me else None,
            host=this_host(),
            started_at=now,
            updated_at=now,
            args_sha256=args_sha256,
            args=args,
            head_before=head_before,
        )
        path = self._dir(workspace) / f"{record.op_id}.json"

        try:
            atomic_write_bytes_new(path, record.model_dump_json().encode())
        except OSError as exc:
            raise JournalError(f"cannot record the intent of {tool}: {exc}") from exc

        return OperationHandle(self, record, path)

    # ------------------------------------------------------------- reading

    def list(self, workspace: Path | str | None = None) -> list[OperationRecord]:
        directories = [self._dir(workspace)] if workspace is not None else (
            [d for d in self.root.iterdir() if d.is_dir()] if self.root.exists() else []
        )
        found = []

        for directory in directories:
            for path in sorted(directory.glob("op-*.json")):
                try:
                    found.append(OperationRecord.model_validate(json.loads(path.read_bytes())))
                except (OSError, ValueError):
                    continue

        return found

    def path_of(self, record: OperationRecord) -> Path:
        return self.root / record.workspace_id / f"{record.op_id}.json"

    def save(self, record: OperationRecord) -> None:
        record.updated_at = time.time()
        atomic_write_bytes_new(self.path_of(record), record.model_dump_json().encode())

    def remove(self, record: OperationRecord) -> None:
        self.path_of(record).unlink(missing_ok=True)


def atomic_write_bytes_new(path: Path, payload: bytes) -> None:
    """Create or replace `path` atomically (parent directories are created)."""

    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        atomic_write_bytes(path, payload)
        return

    # atomic_write_bytes needs an existing target for permission copying;
    # write via a temp file and rename for new files.
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")

    try:
        tmp.write_bytes(payload)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
