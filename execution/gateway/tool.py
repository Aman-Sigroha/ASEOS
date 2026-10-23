from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from runtime.schemas.action import ActionType
from runtime.schemas.execution import ErrorCategory


@dataclass(frozen=True)
class ToolContext:
    """Trusted context supplied by the gateway, never by the model."""

    workspace: Path
    action_id: str = ""
    capabilities: frozenset[str] = frozenset()


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


@dataclass
class ToolOutput:
    """Successful outcome of a tool run."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = 0
    data: dict[str, Any] = field(default_factory=dict)
    truncated: bool = False


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
    ) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.stdout = stdout
        self.stderr = message if stderr is None else stderr
        self.exit_code = exit_code
        self.data = data or {}


class Tool(ABC):
    """A registered, schema-validated operation."""

    spec: ToolSpec

    @abstractmethod
    def run(self, context: ToolContext, args: Any) -> ToolOutput:
        """Run synchronously with validated args; raise ToolError on failure."""
        raise NotImplementedError


def resolve_in_workspace(workspace: Path, relative_path: str) -> Path:
    """Resolve a workspace-relative path, rejecting escapes."""

    candidate = (workspace / relative_path).resolve()

    if not candidate.is_relative_to(workspace):
        raise ToolError(
            "PERMISSION_DENIED", "Path escapes the configured workspace"
        )

    return candidate


_ENV_TEMPLATE_SUFFIXES = (".example", ".sample", ".template")


def ensure_writable_path(workspace: Path, target: Path) -> None:
    """Deny writes to Git internals and secret-bearing env files.

    `target` must already be resolved and inside `workspace`.
    """

    relative = target.relative_to(workspace)

    if ".git" in relative.parts:
        raise ToolError(
            "PERMISSION_DENIED", "Writing inside .git is not permitted"
        )

    name = relative.name

    if name == ".env" or (
        name.startswith(".env.") and not name.endswith(_ENV_TEMPLATE_SUFFIXES)
    ):
        raise ToolError(
            "PERMISSION_DENIED",
            f"Writing to protected file '{name}' is not permitted",
        )
