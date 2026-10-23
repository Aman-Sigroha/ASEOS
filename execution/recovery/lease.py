"""Exclusive write lease on a workspace.

At most one live gateway may mutate a given workspace (a managed worktree
belongs to one task). The lease is a small JSON file created with O_EXCL;
it records who holds it (owner id, pid + creation token, host, task) and a
heartbeat refreshed whenever the owner acts.

A lease is *stale* when its owner can be shown to be gone:
- same host: the recorded process no longer exists (or is a different
  process reusing the pid);
- another host: the heartbeat is older than `ttl` (liveness cannot be
  checked remotely).
A stale lease is taken over atomically (the stale file is renamed away, so
only one contender wins). A live one is never broken: the caller gets a
`LeaseConflict` describing the holder.
"""

import json
import os
import time
import uuid
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from execution.artifacts.store import workspace_id
from execution.patches.fileio import atomic_write_bytes
from execution.recovery.liveness import identity_of, is_same_process
from execution.recovery.locations import lease_name, leases_dir, this_host

DEFAULT_TTL_SECONDS = 120.0


class LeaseConflict(Exception):
    def __init__(self, holder: dict[str, Any]) -> None:
        super().__init__(
            "The workspace is being modified by another running task "
            f"(owner {holder.get('owner_id')}, pid {holder.get('pid')}, "
            f"task {holder.get('task_id')})."
        )
        self.holder = holder


class LeaseLost(Exception):
    """This owner no longer holds the lease (it was taken over)."""


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None

    return data if isinstance(data, dict) else None


def lease_status(data: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    """Describe a lease file's content, including whether its owner is alive."""

    now = time.time() if now is None else now
    ttl = float(data.get("ttl", DEFAULT_TTL_SECONDS))
    age = now - float(data.get("heartbeat_at", 0))
    same_host = data.get("host") == this_host()

    if same_host:
        alive = is_same_process(int(data.get("pid", 0)), data.get("pid_created"))
        stale = not alive
    else:
        alive = None
        stale = age > ttl

    return {**data, "heartbeat_age_seconds": round(age, 1), "owner_alive": alive, "stale": stale}


def read_lease(state_dir: Path, workspace: Path | str, *, now: float | None = None) -> dict[str, Any] | None:
    data = _read(leases_dir(state_dir) / lease_name(workspace))
    return lease_status(data, now=now) if data else None


def all_leases(state_dir: Path, *, now: float | None = None) -> list[dict[str, Any]]:
    found = []

    for path in sorted(leases_dir(state_dir).glob("*.json")):
        data = _read(path)

        if data:
            found.append(lease_status(data, now=now))

    return found


def live_holder(state_dir: Path, workspace: Path | str) -> dict[str, Any] | None:
    """The lease of a still-running owner of `workspace`, if any."""

    status = read_lease(state_dir, workspace)
    return status if status and not status["stale"] else None


@dataclass
class _Held:
    path: Path
    owner_id: str


def _release_file(path: Path, owner_id: str) -> None:
    data = _read(path)

    if data and data.get("owner_id") == owner_id:
        try:
            path.unlink()
        except OSError:
            pass


class WorkspaceLease:
    def __init__(
        self,
        state_dir: Path,
        workspace: Path | str,
        *,
        owner_id: str | None = None,
        task_id: str | None = None,
        ttl: float = DEFAULT_TTL_SECONDS,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.owner_id = owner_id or f"owner-{uuid.uuid4().hex[:12]}"
        self.task_id = task_id
        self.ttl = ttl
        self.path = leases_dir(state_dir) / lease_name(self.workspace)
        self.held = False
        self._finalizer: weakref.finalize | None = None

    # ----------------------------------------------------------------- body

    def _body(self, acquired_at: float | None = None) -> bytes:
        me = identity_of(os.getpid())
        now = time.time()
        return json.dumps(
            {
                "workspace_id": workspace_id(self.workspace),
                "workspace": str(self.workspace),
                "owner_id": self.owner_id,
                "pid": os.getpid(),
                "pid_created": me.created if me else None,
                "host": this_host(),
                "task_id": self.task_id,
                "acquired_at": acquired_at or now,
                "heartbeat_at": now,
                "ttl": self.ttl,
            }
        ).encode()

    def _create(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)

        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False

        with os.fdopen(fd, "wb") as handle:
            handle.write(self._body())

        return True

    # ------------------------------------------------------------------ api

    def acquire(self) -> None:
        """Take (or refresh) the lease; raises LeaseConflict if a live owner holds it."""

        if self.held:
            self.heartbeat()
            return

        for _ in range(5):
            if self._create():
                self._mark_held()
                return

            current = _read(self.path)

            if current is None:
                # Unreadable or vanished: treat a corrupt file as stale, retry on vanish.
                if not self.path.exists():
                    continue

                current = {}

            status = lease_status(current) if current else {"stale": True}

            if current and current.get("owner_id") == self.owner_id:
                self._mark_held()
                self.heartbeat()
                return

            if not status["stale"]:
                raise LeaseConflict(status)

            # Stale: rename it away so exactly one contender proceeds.
            aside = self.path.with_name(f"{self.path.name}.stale-{uuid.uuid4().hex[:8]}")

            try:
                os.replace(self.path, aside)
            except OSError:
                continue

            try:
                aside.unlink()
            except OSError:
                pass

        raise LeaseConflict({"owner_id": "unknown", "note": "lease contention"})

    def _mark_held(self) -> None:
        self.held = True
        self._finalizer = weakref.finalize(
            self, _release_file, self.path, self.owner_id
        )

    def heartbeat(self) -> None:
        current = _read(self.path)

        if not current or current.get("owner_id") != self.owner_id:
            self.held = False
            raise LeaseLost(
                "The workspace lease was taken over; this task must stop modifying the workspace."
            )

        atomic_write_bytes(self.path, self._body(current.get("acquired_at")))

    def release(self) -> None:
        if self._finalizer is not None:
            self._finalizer.detach()
            self._finalizer = None

        if self.held:
            _release_file(self.path, self.owner_id)

        self.held = False
