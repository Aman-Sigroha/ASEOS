"""Where recovery state (leases, the operations journal) lives.

Inside the repository's common git directory for Git workspaces, so every
linked worktree and every gateway on the repository sees the same state and
the working tree never does; a per-workspace temp folder otherwise.
"""

import hashlib
import socket
import tempfile
from pathlib import Path

from execution.artifacts.store import workspace_id
from execution.git.repo import GitError, run_git


def state_dir_for(workspace: Path | str) -> Path:
    path = Path(workspace).resolve()

    try:
        common = run_git(path, ["rev-parse", "--git-common-dir"]).stdout.strip()
        return (path / common).resolve() / "aseos"
    except (GitError, OSError):
        return Path(tempfile.gettempdir()) / "aseos-state" / workspace_id(path)


def leases_dir(state_dir: Path) -> Path:
    return state_dir / "leases"


def ops_dir(state_dir: Path) -> Path:
    return state_dir / "ops"


def lease_name(workspace: Path | str) -> str:
    """File name for a workspace's lease (stable, case-insensitive on Windows)."""

    return f"{workspace_id(workspace)}.json"


def this_host() -> str:
    return hashlib.sha256(socket.gethostname().encode()).hexdigest()[:12]
