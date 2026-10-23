from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class SandboxError(Exception):
    """The sandbox cannot (safely) run this request."""


def _noop() -> None:
    return None


@dataclass
class PreparedCommand:
    """What actually gets started on the host to run a request in a sandbox."""

    command: list[str]
    env: dict[str, str]
    description: str
    # Stop the sandboxed program itself (killing the host-side client is not
    # enough for a container: it would keep running).
    terminate: Callable[[], None] = _noop
    cleanup: Callable[[], None] = _noop
    details: dict[str, Any] = field(default_factory=dict)


class Sandbox(ABC):
    """Runs a program with restricted filesystem, network and resources."""

    name: str = "sandbox"

    @abstractmethod
    def availability(self) -> tuple[bool, str]:
        """(usable, reason). Must be cheap enough to call before each run."""

    @abstractmethod
    def prepare(
        self,
        argv: list[str],
        *,
        cwd: Path,
        workspace: Path,
        env: Mapping[str, str],
        limits: Any,
        network: bool,
        run_id: str,
    ) -> PreparedCommand:
        """Build the host command. Raises SandboxError for unsafe requests."""
