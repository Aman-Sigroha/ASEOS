
import asyncio
import time
from pathlib import Path
from typing import Any

from execution.commands.runner import run_command
from execution.patches.apply import apply_patch
from execution.testing.runner import run_tests
from runtime.agent.executor import ActionExecutor
from runtime.schemas.action import Action
from runtime.schemas.execution import ExecutionResult


class RealActionExecutor(ActionExecutor):
    """Execute supported actions inside a configured workspace."""

    def __init__(self, workspace_path: str) -> None:
        self.workspace = Path(workspace_path).resolve()

        if not self.workspace.is_dir():
            raise ValueError(
                f"Workspace must be an existing directory: {self.workspace}"
            )

    def _resolve_file(self, relative_path: Any) -> Path:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise ValueError("'path' must be a non-empty string")

        candidate = (self.workspace / relative_path).resolve()

        if not candidate.is_relative_to(self.workspace):
            raise ValueError("Path escapes the configured workspace")

        return candidate

    def _execute_sync(
        self, action: Action
    ) -> tuple[bool, str, str, int | None]:
        payload = action.payload

        if action.type == "READ":
            file_path = self._resolve_file(payload.get("path"))

            if not file_path.is_file():
                raise ValueError("Requested file does not exist")

            content = file_path.read_text(encoding="utf-8")
            return True, content, "", 0

        if action.type == "SEARCH":
            query = payload.get("query")

            if not isinstance(query, str) or not query.strip():
                raise ValueError("'query' must be a non-empty string")

            search_root = self._resolve_file(payload.get("path", "."))

            if not search_root.exists():
                raise ValueError("Search path does not exist")

            if search_root.is_file():
                files = [search_root]
            elif search_root.is_dir():
                ignored = {
                    ".git", ".venv", "venv", "__pycache__", "node_modules"
                }
                files = []

                for path in search_root.rglob("*"):
                    if not path.is_file():
                        continue

                    if any(part in ignored for part in path.parts):
                        continue

                    resolved = path.resolve()

                    if resolved.is_relative_to(self.workspace):
                        files.append(resolved)
            else:
                raise ValueError("Search path is not a file or directory")

            matches = []

            for file_path in files:
                try:
                    lines = file_path.read_text(
                        encoding="utf-8"
                    ).splitlines()

                    for line_number, line in enumerate(lines, start=1):
                        if query.casefold() in line.casefold():
                            relative = file_path.relative_to(self.workspace)
                            matches.append(
                                f"{relative}:{line_number}: {line}"
                            )

                            if len(matches) >= 100:
                                break
                except (UnicodeError, OSError):
                    continue

                if len(matches) >= 100:
                    break

            output = "\n".join(matches) if matches else "No matches found."
            return True, output, "", 0

        if action.type == "EDIT":
            result = apply_patch(str(self.workspace), payload)
            success = result.get("status") == "PASS"
            message = result.get("message", "Patch operation failed")

            return (
                success,
                message,
                "" if success else message,
                0 if success else 1,
            )

        if action.type == "RUN_TEST":
            result = run_tests(str(self.workspace))
            success = result.get("status") == "PASS"

            return (
                success,
                result.get("stdout") or "",
                result.get("stderr") or "",
                result.get("exit_code"),
            )

        if action.type == "GIT_DIFF":
            result = run_command(
                ["git", "diff", "--"],
                cwd=str(self.workspace),
                timeout=10,
            )
            success = result.get("status") == "PASS"

            return (
                success,
                result.get("stdout") or "",
                result.get("stderr") or "",
                result.get("exit_code"),
            )

        if action.type == "RUN_COMMAND":
            message = (
                "RUN_COMMAND is disabled. It requires a separate, "
                "explicitly approved command-execution policy."
            )
            return False, "", message, None

        return False, "", f"Unsupported action type: {action.type}", None

    async def execute(self, action: Action) -> ExecutionResult:
        started = time.perf_counter()

        try:
            success, stdout, stderr, exit_code = await asyncio.to_thread(
                self._execute_sync, action
            )
        except Exception as exc:
            success = False
            stdout = ""
            stderr = f"{type(exc).__name__}: {exc}"
            exit_code = None

        duration_ms = int((time.perf_counter() - started) * 1000)

        return ExecutionResult(
            action_id=action.id,
            success=success,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_ms=duration_ms,
        )
