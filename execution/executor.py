from pathlib import Path
from uuid import uuid4

from execution.git.diff import get_diff
from execution.git.repo import GitError
from execution.patches.apply import apply_patch
from execution.testing.runner import run_tests
from execution.worktrees.manager import WorktreeManager
from runtime.events.emitter import EventEmitter
from runtime.events.event import AgentEvent


def execute_task(
    repo_path: str,
    task_id: str,
    patch: dict,
    event_emitter: EventEmitter | None = None,
    keep_worktree: bool | None = None,
    manager: WorktreeManager | None = None,
) -> dict:
    """Execute a patch in an isolated worktree and report its result.

    The worktree is kept whenever it holds work worth reviewing (a
    successful or failed patch/test run). With `keep_worktree=None` it is
    removed only when it is clean and has no unmerged commits; with
    `keep_worktree=False` removal is attempted but still refused if it
    would lose work. The result's `worktree` entry says what happened.
    """

    def emit_event(
        event_type: str,
        message: str,
        data: dict,
    ) -> None:
        if event_emitter is not None:
            event_emitter.emit(
                AgentEvent(
                    event_id=str(uuid4()),
                    task_id=task_id,
                    event_type=event_type,
                    message=message,
                    data=data,
                )
            )

    manager = manager or WorktreeManager(repo_path)
    record = manager.create(task_id)
    worktree = record.path
    outcome = "failed"

    try:
        patch_result = apply_patch(worktree, patch)

        if patch_result["status"] != "PASS":
            emit_event(
                "ACTION_FAILED",
                "Code patch could not be applied",
                {"patch": patch_result},
            )
            return _finish(
                manager,
                record,
                keep_worktree,
                outcome,
                {
                    "status": "ERROR",
                    "patch": patch_result,
                    "tests": None,
                    "diff": "",
                },
            )

        emit_event(
            "ACTION_COMPLETED",
            "Code patch applied successfully",
            {"patch": patch_result},
        )

        emit_event(
            "VERIFICATION_STARTED",
            "Running tests against the patched worktree",
            {},
        )

        test_result = run_tests(worktree)
        diff = get_diff(
            Path(record.path),
            mode="base",
            base=record.base_commit,
            include_untracked=True,
            max_bytes=50_000_000,
        )["diff"]

        status = (
            "PASS"
            if test_result["status"] == "PASS"
            else test_result["status"]
        )

        result = {
            "status": status,
            "patch": patch_result,
            "tests": test_result,
            "diff": diff,
        }

        if status == "PASS":
            outcome = "completed"
            emit_event(
                "VERIFICATION_COMPLETED",
                "Tests passed",
                {"status": status, "tests": test_result},
            )
        else:
            emit_event(
                "VERIFICATION_FAILED",
                "Tests did not pass",
                {"status": status, "tests": test_result},
            )

        return _finish(manager, record, keep_worktree, outcome, result)

    except Exception as exc:
        emit_event(
            "ACTION_FAILED",
            "Code execution encountered an error",
            {"error": str(exc)},
        )
        _safe_mark(manager, record.worktree_id, "failed", str(exc))
        raise


def _safe_mark(manager: WorktreeManager, worktree_id: str, status, note=""):
    try:
        manager.mark(worktree_id, status, note)
    except GitError:
        pass


def _finish(
    manager: WorktreeManager,
    record,
    keep_worktree: bool | None,
    outcome: str,
    result: dict,
) -> dict:
    """Record the outcome, then clean up only when that loses nothing."""

    _safe_mark(manager, record.worktree_id, outcome)
    removed = False
    note = None
    assessment = manager.assess(manager.get(record.worktree_id))

    if keep_worktree is not True and (
        keep_worktree is False or not assessment["unreviewed"]
    ):
        try:
            manager.remove(record.worktree_id)
            removed = True
        except GitError as exc:
            note = exc.message

    result["worktree"] = {
        "id": record.worktree_id,
        "path": record.path,
        "branch": record.branch,
        "base_commit": record.base_commit,
        "status": "removed" if removed else outcome,
        "preserved": not removed,
        "note": note,
    }
    return result
