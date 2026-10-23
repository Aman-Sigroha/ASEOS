import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from execution.gateway.capabilities import CAP_FS_READ_SECRETS
from execution.security.levels import ExecutionLevel
from execution.security.paths import (
    raw_path_problem,
    read_denied_reason,
    write_denied_reason,
)
from runtime.schemas.action import ActionType
from runtime.schemas.execution import ArtifactRef, ErrorCategory


@dataclass(frozen=True)
class ToolContext:
    """Trusted context supplied by the gateway, never by the model."""

    workspace: Path
    action_id: str = ""
    capabilities: frozenset[str] = frozenset()
    max_level: ExecutionLevel = ExecutionLevel.ELEVATED
    # Isolation for programs a tool starts (see execution/sandbox). When
    # `require_sandbox` is set and no usable sandbox exists, programs are
    # refused instead of being run on the host.
    sandbox: Any = None
    require_sandbox: bool = False
    # Set by the gateway on cancellation, timeout or shutdown. Tools that
    # run for a while must watch it and stop (and clean up) promptly.
    cancel: threading.Event = field(default_factory=threading.Event)


@dataclass(frozen=True)
class ToolSpec:
    """Static description of a tool: identity, schema and constraints."""

    name: str
    action_type: ActionType
    description: str
    args_model: type[BaseModel]
    capabilities: frozenset[str]
    read_only: bool
    idempotent: bool = False
    timeout_seconds: float = 120.0
    # Unclassified tools default to the highest level so a restricted
    # profile never runs something nobody has assessed.
    level: ExecutionLevel = ExecutionLevel.ELEVATED


@dataclass
class ToolOutput:
    """Successful outcome of a tool run."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = 0
    data: dict[str, Any] = field(default_factory=dict)
    truncated: bool = False
    artifacts: list[ArtifactRef] = field(default_factory=list)


class ToolError(Exception):
    """Categorized tool failure, converted to a failed ExecutionResult."""

    def __init__(
        self,
        category: ErrorCategory,
        message: str,
        *,
        stdout: str = "",
        stderr: str | None = None,
        exit_code: int | None = None,
        data: dict[str, Any] | None = None,
        artifacts: list[ArtifactRef] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.stdout = stdout
        self.stderr = message if stderr is None else stderr
        self.exit_code = exit_code
        self.data = data or {}
        self.artifacts = artifacts or []


class Tool(ABC):
    """A registered, schema-validated operation."""

    spec: ToolSpec

    @abstractmethod
    def run(self, context: ToolContext, args: Any) -> ToolOutput:
        """Run synchronously with validated args; raise ToolError on failure."""
        raise NotImplementedError


def resolve_in_workspace(workspace: Path, relative_path: str) -> Path:
    """Resolve a workspace-relative path, rejecting malformed paths and escapes."""

    problem = raw_path_problem(relative_path)

    if problem:
        raise ToolError("INVALID_ARGUMENTS", f"Invalid path: {problem}")

    try:
        candidate = (workspace / relative_path).resolve()
    except (OSError, ValueError, RuntimeError) as exc:
        raise ToolError("INVALID_ARGUMENTS", f"Invalid path: {exc}") from exc

    if not candidate.is_relative_to(workspace):
        raise ToolError(
            "PERMISSION_DENIED", "Path escapes the configured workspace"
        )

    return candidate


def ensure_writable_path(workspace: Path, target: Path) -> None:
    """Deny writes to Git internals, the managed worktree area and env files.

    `target` must already be resolved and inside `workspace`.
    """

    reason = write_denied_reason(target.relative_to(workspace))

    if reason:
        raise ToolError("PERMISSION_DENIED", reason)


def ensure_readable_path(
    workspace: Path,
    target: Path,
    capabilities: frozenset[str] | set[str] = frozenset(),
) -> None:
    """Deny showing the content of secret files and other tasks' worktrees."""

    reason = read_denied_reason(
        target.relative_to(workspace), CAP_FS_READ_SECRETS in capabilities
    )

    if reason:
        raise ToolError("PERMISSION_DENIED", reason)
