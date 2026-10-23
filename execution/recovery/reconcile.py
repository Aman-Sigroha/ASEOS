"""Reconcile interrupted operations against what is really on disk.

Nothing here re-runs, rolls back or deletes work. For every operation whose
owner is gone it compares the checkpoints written before the action with the
actual files, Git HEAD and child process, and reports one of:

    not_applied   no change reached the disk          -> safe to retry
    applied       the full change is on disk          -> do not re-run
    partial       some files changed, some did not    -> inspect first
    diverged      files match neither state           -> inspect first
    committed     HEAD moved to the intended commit   -> do not re-run
    not_committed HEAD did not move                   -> safe to retry
    head_moved    HEAD changed, but not as intended   -> inspect first
    unknown       no checkpoint (e.g. a command)      -> inspect first
"""

import hashlib
import time
from pathlib import Path
from typing import Any

from execution.artifacts.store import workspace_id
from execution.git.repo import GitError, run_git
from execution.recovery.liveness import is_same_process, is_verified, terminate_tree
from execution.recovery.locations import this_host
from execution.recovery.ops import OperationRecord, OperationsLog

REMOTE_OWNER_GRACE_SECONDS = 3600.0


def _sha_of(workspace: Path, relative: str) -> tuple[str | None, bool]:
    """(sha256 or None if absent, path_is_safe)."""

    target = (workspace / relative).resolve()

    try:
        target.relative_to(workspace.resolve())
    except ValueError:
        return None, False

    try:
        return hashlib.sha256(target.read_bytes()).hexdigest(), True
    except FileNotFoundError:
        return None, True
    except OSError:
        return "unreadable", True


def _head(workspace: Path) -> str | None:
    try:
        return run_git(workspace, ["rev-parse", "HEAD"], timeout=10).stdout.strip() or None
    except (GitError, OSError):
        return None


def _last_subject(workspace: Path) -> str | None:
    try:
        return run_git(workspace, ["log", "-1", "--format=%s"], timeout=10).stdout.strip()
    except (GitError, OSError):
        return None


def _assess_files(workspace: Path, checkpoint: dict[str, Any]) -> dict[str, Any]:
    states: dict[str, str] = {}

    for item in checkpoint.get("files", []):
        path = item.get("path", "")
        current, safe = _sha_of(workspace, path)

        if not safe:
            states[path] = "diverged"
        elif current == item.get("before"):
            states[path] = "unchanged"
        elif current == item.get("after"):
            states[path] = "applied"
        else:
            states[path] = "diverged"

    values = set(states.values())

    if not states or values == {"unchanged"}:
        outcome = "not_applied"
        advice = (
            "No change reached the disk; it is safe to retry the action. Pass the "
            "returned `base` hashes so the retry cannot apply on top of a changed file."
        )
    elif values == {"applied"}:
        outcome = "applied"
        advice = "The complete change is already on disk. Do not re-run it; verify and continue."
    elif "diverged" in values:
        outcome = "diverged"
        advice = (
            "Some files match neither the expected before nor after state (edited since, or "
            "partly written). Inspect `git diff` before acting; do not re-apply blindly."
        )
    else:
        outcome = "partial"
        advice = (
            "Only some files were changed. Re-applying the same patch would conflict on the "
            "changed files: inspect, then revert them or apply only the missing files."
        )

    return {
        "outcome": outcome,
        "resumable": outcome == "not_applied",
        "recommendation": advice,
        "files": states,
        "patch_id": checkpoint.get("patch_id"),
        # Pass these as `base` when retrying: a diff with little context can
        # otherwise match an already-changed file and apply a second time.
        "base": {
            item["path"]: item["before"]
            for item in checkpoint.get("files", [])
            if item.get("before")
        },
    }


def _assess_commit(workspace: Path, record: OperationRecord, checkpoint: dict[str, Any]) -> dict[str, Any]:
    now = _head(workspace)
    before = record.head_before

    if now == before:
        return {
            "outcome": "not_committed",
            "resumable": True,
            "recommendation": "HEAD did not move; the commit did not happen. Retrying is safe.",
            "head_before": before,
            "head_now": now,
        }

    subject = _last_subject(workspace)
    intended = str(checkpoint.get("message", "")).splitlines()[0:1]
    matches = bool(intended) and subject == intended[0]
    return {
        "outcome": "committed" if matches else "head_moved",
        "resumable": False,
        "recommendation": (
            "The commit exists. Do not commit again."
            if matches
            else "HEAD changed but the latest commit is not the intended one. Inspect `git log`."
        ),
        "head_before": before,
        "head_now": now,
        "subject": subject,
    }


def _assess_process(record: OperationRecord, *, terminate: bool) -> dict[str, Any] | None:
    if not record.process:
        return None

    pid, created = int(record.process["pid"]), record.process.get("created")
    info: dict[str, Any] = {"pid": pid, "run_id": record.process.get("run_id")}

    if not is_same_process(pid, created):
        return {**info, "state": "gone"}

    if not is_verified(pid, created):
        return {**info, "state": "alive_unverified", "note": "identity cannot be confirmed; not touched"}

    if not terminate:
        return {**info, "state": "orphan_running"}

    return {**info, "state": "orphan_terminated" if terminate_tree(pid, created) else "orphan_survived"}


def assess_operation(
    record: OperationRecord, workspace: Path, *, terminate_orphans: bool = False
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "outcome": "unknown",
        "resumable": False,
        "recommendation": (
            "No checkpoint was recorded, so the effects are unknown. Inspect `git status` and "
            "the files involved; do not retry automatically."
        ),
    }
    checkpoints = {c.get("kind"): c for c in record.checkpoints}

    if "files" in checkpoints:
        result.update(_assess_files(workspace, checkpoints["files"]))
    elif "commit" in checkpoints:
        result.update(_assess_commit(workspace, record, checkpoints["commit"]))
    elif record.process:
        head_now = _head(workspace)
        result["head_before"], result["head_now"] = record.head_before, head_now
        result["head_changed"] = head_now != record.head_before

    process = _assess_process(record, terminate=terminate_orphans)

    if process is not None:
        result["process"] = process

        if process["state"] in ("orphan_running", "alive_unverified", "orphan_survived"):
            result["resumable"] = False
            result["recommendation"] = (
                "A process started by the interrupted action is still running. Stop it "
                "(reconcile with terminate_orphans) before doing anything else."
            )

    return result


def _summary(record: OperationRecord) -> dict[str, Any]:
    return {
        "op_id": record.op_id,
        "action_id": record.action_id,
        "tool": record.tool,
        "task_id": record.task_id,
        "owner_id": record.owner_id,
        "pid": record.pid,
        "started_at": record.started_at,
        "state": record.state,
        "assessment": record.assessment,
    }


def reconcile_operations(
    log: OperationsLog,
    workspace: Path,
    *,
    self_owner_id: str,
    finished_actions: set[str] | None = None,
    terminate_orphans: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """Classify every journaled operation of `workspace`; never re-runs anything."""

    now = time.time() if now is None else now
    report: dict[str, Any] = {
        "running": [],
        "running_elsewhere": [],
        "interrupted": [],
        "cleaned": 0,
    }
    host = this_host()

    for record in log.list(workspace):
        if record.host == host:
            owner_alive = is_same_process(record.pid, record.pid_created)
        else:
            owner_alive = now - record.updated_at < REMOTE_OWNER_GRACE_SECONDS

        if owner_alive:
            key = "running" if record.owner_id == self_owner_id else "running_elsewhere"
            report[key].append(_summary(record))
            continue

        if finished_actions is not None and record.action_id in finished_actions:
            log.remove(record)  # it ended; only the journal cleanup was missed
            report["cleaned"] += 1
            continue

        record.state = "interrupted"
        record.assessment = assess_operation(
            record, Path(record.workspace), terminate_orphans=terminate_orphans
        )
        log.save(record)
        report["interrupted"].append(_summary(record))

    return report


def acknowledge(log: OperationsLog, workspace: Path, op_id: str) -> bool:
    """The host has dealt with an interrupted operation: remove it from the table."""

    for record in log.list(workspace):
        if record.op_id == op_id and record.workspace_id == workspace_id(workspace):
            log.remove(record)
            return True

    return False
