import asyncio
import time

from runtime.state.state import AgentState
from runtime.verification.result import (
    VerificationCheck,
    VerificationResult,
)
from runtime.verification.verifier import Verifier


class CommandVerifier(Verifier):
    def __init__(
        self,
        commands: list[str],
        timeout_seconds: float = 30.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than 0.")

        self.commands = commands
        self.timeout_seconds = timeout_seconds

    async def _run_command(
        self,
        command: str,
        workspace_path: str,
    ) -> VerificationCheck:
        started = time.perf_counter()

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=workspace_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (OSError, ValueError) as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)

            return VerificationCheck(
                name=command,
                status="ERROR",
                message=str(exc),
                duration_ms=duration_ms,
            )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()

            duration_ms = int((time.perf_counter() - started) * 1000)

            return VerificationCheck(
                name=command,
                status="TIMEOUT",
                message=(f"Command exceeded the {self.timeout_seconds:g}s timeout."),
                duration_ms=duration_ms,
            )

        duration_ms = int((time.perf_counter() - started) * 1000)

        stdout_text = stdout.decode(errors="replace").strip()
        stderr_text = stderr.decode(errors="replace").strip()

        if process.returncode == 0:
            message = stdout_text or "Command completed successfully."
            status = "PASS"
        else:
            message_parts = []

            if stderr_text:
                message_parts.append(stderr_text)

            if stdout_text:
                message_parts.append(stdout_text)

            message = "\n".join(message_parts)
            if not message:
                message = f"Command exited with code {process.returncode}."

            status = "FAIL"

        return VerificationCheck(
            name=command,
            status=status,
            message=message,
            duration_ms=duration_ms,
        )

    async def verify(self, state: AgentState) -> VerificationResult:
        if not self.commands:
            return VerificationResult(
                status="ERROR",
                summary="No verification commands were provided.",
            )

        checks: list[VerificationCheck] = []

        for command in self.commands:
            check = await self._run_command(
                command=command,
                workspace_path=state.task.workspace_path,
            )
            checks.append(check)

        statuses = {check.status for check in checks}

        if "TIMEOUT" in statuses:
            overall_status = "TIMEOUT"
            summary = "Verification timed out."
        elif "ERROR" in statuses:
            overall_status = "ERROR"
            summary = "Verification encountered an execution error."
        elif "FAIL" in statuses:
            overall_status = "FAIL"
            summary = "One or more verification commands failed."
        else:
            overall_status = "PASS"
            summary = "All verification commands passed."

        return VerificationResult(
            status=overall_status,
            checks=checks,
            summary=summary,
        )
