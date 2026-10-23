"""Helpers for building real throwaway Git repositories in tests."""

import subprocess
from pathlib import Path

from execution.gateway import ExecutionGateway
from execution.gateway.capabilities import CAP_GIT_DISCARD, CAP_GIT_WRITE
from execution.gateway.tools import DEFAULT_CAPABILITIES, default_tools
from runtime.schemas.action import Action

WRITE_CAPS = DEFAULT_CAPABILITIES | {CAP_GIT_WRITE}
ALL_GIT_CAPS = WRITE_CAPS | {CAP_GIT_DISCARD}


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout


def write(repo: Path, name: str, text: str) -> Path:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def init_repo(root: Path, *, commit: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    repo = root.resolve()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "ASEOS Test")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "config", "core.autocrlf", "false")

    if commit:
        write(repo, "a.txt", "one\n")
        write(repo, "b.txt", "bee\n")
        git(repo, "add", ".")
        git(repo, "commit", "-m", "initial commit")

    return repo


def make_gateway(repo: Path, capabilities=DEFAULT_CAPABILITIES):
    return ExecutionGateway(
        str(repo),
        tools=default_tools(),
        granted_capabilities=capabilities,
    )


async def run(gateway, action_type, payload=None, action_id="act-1"):
    return await gateway.execute(
        Action(id=action_id, type=action_type, payload=payload or {})
    )
