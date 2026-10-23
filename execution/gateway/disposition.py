"""What a caller should do about a failed action, and which actions need approval.

The gateway reports a *disposition* on every failed result so the runtime does
not have to guess from free text:

  RETRY     transient and safe to repeat unchanged (the action never started,
            or the tool is idempotent). The RealActionExecutor retries these
            a bounded number of times on its own.
  REPLAN    the action ran and was rejected on its merits (stale patch, failing
            tests, bad arguments). Repeating it would fail the same way.
  ESCALATE  a human decision is needed (a capability must be granted, or the
            tool itself is broken).
  ABORT     stop the task (it was cancelled).

Approval is derived from what a tool needs, not from a hand-kept list: a tool
that changes state and needs anything beyond the default grant asks first.
"""

from typing import Any

from execution.gateway.capabilities import DEFAULT_CAPABILITIES
from execution.gateway.tool import ToolSpec
from execution.security.levels import ExecutionLevel

RETRY = "RETRY"
REPLAN = "REPLAN"
ESCALATE = "ESCALATE"
ABORT = "ABORT"

# Failures that can clear up by themselves.
TRANSIENT = frozenset({"WORKSPACE_UNAVAILABLE", "TIMEOUT"})


def requires_approval(spec: ToolSpec) -> bool:
    """True for tools that change state and are not covered by the default grant."""

    if spec.read_only:
        return False

    return bool(spec.capabilities - DEFAULT_CAPABILITIES) or (
        spec.level >= ExecutionLevel.ELEVATED
    )


def disposition_of(
    category: str | None,
    data: dict[str, Any],
    spec: ToolSpec | None,
) -> dict[str, Any]:
    """The disposition of one failure. `data` is the failed result's data."""

    not_started = bool(data.get("not_started"))
    idempotent = bool(spec and (spec.read_only or spec.idempotent))

    def verdict(action: str, reason: str) -> dict[str, Any]:
        return {"action": action, "reason": reason, "not_started": not_started}

    if category == "CANCELLED":
        return verdict(ABORT, "The action was cancelled.")

    if category == "PERMISSION_DENIED":
        return verdict(
            ESCALATE,
            "A capability or execution level has not been granted; a trusted "
            "caller must approve it before this can run.",
        )

    if category == "INTERNAL_ERROR":
        return verdict(ESCALATE, "The tool failed unexpectedly; this is not something a new plan fixes.")

    if category in TRANSIENT and (not_started or idempotent):
        return verdict(
            RETRY,
            "Temporarily unavailable"
            + ("; the action never started." if not_started else "; the action is safe to repeat."),
        )

    if category == "TIMEOUT":
        return verdict(
            REPLAN,
            "The action timed out after it started; its effect is unknown, so "
            "inspect the workspace before repeating it.",
        )

    if category == "WORKSPACE_UNAVAILABLE":
        return verdict(REPLAN, "The workspace is unavailable.")

    return verdict(REPLAN, "The action ran and was rejected; repeating it unchanged would fail again.")
