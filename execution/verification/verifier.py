"""Role 1's `Verifier` backed by the execution layer's evidence.

`VerificationResult.status` (PASS/FAIL/ERROR/TIMEOUT) stays the one
authoritative verdict the runtime acts on. The `VerificationReport` is the
evidence behind it, folded deterministically; nothing else (a plan finishing,
an `ExecutionResult.success`, a `TestRunResult`) decides the outcome.
"""

from collections.abc import Callable
from pathlib import Path

from execution.gateway.dispatcher import ExecutionGateway
from execution.verification.checks import Check
from execution.verification.evidence import CheckEvidence, VerificationReport
from execution.verification.service import run_checks
from runtime.state.state import AgentState
from runtime.verification.result import VerificationCheck, VerificationResult
from runtime.verification.verifier import Verifier

MAX_MESSAGE = 2000

ChecksSource = list[Check] | Callable[[AgentState], list[Check]]


def _fingerprint(version: dict | None) -> str | None:
    return (version or {}).get("fingerprint")


def _role1_check(item: CheckEvidence) -> VerificationCheck:
    return VerificationCheck(
        name=item.name,
        status=item.status,
        message=item.message[:MAX_MESSAGE],
        duration_ms=item.duration_ms,
        kind=item.kind,
        evidence={
            "observed": item.observed,
            "next_steps": list(item.next_steps),
            "modified_workspace": item.modified_workspace,
            "workspace_fingerprint": _fingerprint(item.workspace_version),
            "traces": [
                {
                    "tool": t.tool,
                    "action_id": t.action_id,
                    "success": t.success,
                    "exit_code": t.exit_code,
                    "error_category": t.error_category,
                    "truncated": t.truncated,
                    "artifacts": [a.model_dump() for a in t.artifacts],
                }
                for t in item.traces
            ],
        },
    )


def to_verification_result(report: VerificationReport) -> VerificationResult:
    return VerificationResult(
        status=report.status,
        checks=[_role1_check(item) for item in report.checks],
        summary=report.summary,
        evidence={
            "run_id": report.run_id,
            "workspace": report.workspace,
            "version_before": _fingerprint(report.version_before),
            "version_after": _fingerprint(report.version_after),
            "stable": report.stable,
            "next_steps": list(report.next_steps),
            "skipped": list(report.skipped),
        },
    )


class ExecutionVerifier(Verifier):
    """Verify a task's workspace with typed checks run through the gateway."""

    def __init__(
        self,
        gateway: ExecutionGateway,
        checks: ChecksSource,
        *,
        fail_fast: bool = False,
        require_stable: bool = True,
    ) -> None:
        self.gateway = gateway
        self._checks = checks
        self.fail_fast = fail_fast
        self.require_stable = require_stable
        self.last_report: VerificationReport | None = None

    async def verify(self, state: AgentState) -> VerificationResult:
        task_workspace = Path(state.task.workspace_path).resolve()

        if task_workspace != self.gateway.workspace:
            # Never verify a different tree than the one the task changed.
            return VerificationResult(
                status="ERROR",
                summary=(
                    "The verifier is bound to a different workspace than the task: "
                    f"{self.gateway.workspace} vs {task_workspace}."
                ),
            )

        checks = self._checks(state) if callable(self._checks) else self._checks
        report = await run_checks(
            self.gateway,
            checks,
            fail_fast=self.fail_fast,
            require_stable=self.require_stable,
        )
        self.last_report = report
        return to_verification_result(report)
