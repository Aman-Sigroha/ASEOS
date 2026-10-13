from uuid import uuid4

from execution.git.worktree import create_worktree, remove_worktree
from execution.patches.apply import apply_patch
from execution.testing.runner import run_tests
from execution.git.operations import git_diff
from runtime.events.emitter import EventEmitter
from runtime.events.event import AgentEvent


def execute_task(
    repo_path: str,
    task_id: str,
    patch: dict,
    event_emitter: EventEmitter | None = None,
) -> dict:
    """Execute a patch in an isolated worktree and report its result."""

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

    worktree = None

    try:
        worktree = create_worktree(repo_path, task_id)

        patch_result = apply_patch(worktree, patch)

        if patch_result["status"] != "PASS":
            emit_event(
                "ACTION_FAILED",
                "Code patch could not be applied",
                {"patch": patch_result},
            )
            return {
                "status": "ERROR",
                "patch": patch_result,
                "tests": None,
                "diff": "",
            }

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
        diff = git_diff(worktree)

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

        return result

    except Exception as exc:
        emit_event(
            "ACTION_FAILED",
            "Code execution encountered an error",
            {"error": str(exc)},
        )
        raise

    finally:
        if worktree is not None:
            remove_worktree(repo_path, task_id)
