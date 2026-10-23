"""Low-level, hardened Git access shared by status, diff and mutations.

Every call goes through `run_git`:
- argv list only (never a shell); `--literal-pathspecs` so paths are never
  treated as globs; `--no-optional-locks` for read-only commands so we do
  not contend with the user's own Git for the index lock.
- `core.fsmonitor` is disabled, because a repository can configure it to
  run an arbitrary program on `git status`.
- Environment variables that could redirect Git (GIT_DIR, ...) are removed
  and prompts are disabled.
- Failures raise `GitError` with a gateway error category.
"""

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from execution.security.paths import raw_path_problem

GIT_TIMEOUT_SECONDS = 20
# Another Git process (or an editor's Git integration) may hold index.lock
# for a moment. Retry a few times; never remove a lock ourselves.
LOCK_RETRY_DELAYS = (0.2, 0.5, 1.0)
LOCK_STALE_AGE_SECONDS = 600.0
_LOCK_PATH = re.compile(r"Unable to create '([^']+\.lock)'")
_REDIRECTING_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


class GitError(Exception):
    """A Git operation failed; `category` is an ExecutionResult category."""

    def __init__(
        self,
        category: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.data = data or {}


@dataclass
class GitResult:
    stdout: str
    stderr: str
    returncode: int
    args: list[str] = field(default_factory=list)


def _environment() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _REDIRECTING_ENV}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["LC_ALL"] = "C"
    return env


def run_git(
    repo: Path | str,
    args: list[str],
    *,
    read_only: bool = True,
    timeout: float = GIT_TIMEOUT_SECONDS,
    ok_codes: tuple[int, ...] = (0,),
) -> GitResult:
    command = ["git", "-C", str(repo), "--literal-pathspecs"]

    if read_only:
        command.append("--no-optional-locks")

    command += ["-c", "core.fsmonitor=false"] + args

    try:
        for attempt in range(len(LOCK_RETRY_DELAYS) + 1):
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=timeout,
                env=_environment(),
                stdin=subprocess.DEVNULL,
            )

            if (
                completed.returncode in ok_codes
                or b".lock" not in completed.stderr
                or attempt == len(LOCK_RETRY_DELAYS)
            ):
                break

            time.sleep(LOCK_RETRY_DELAYS[attempt])
    except FileNotFoundError as exc:
        raise GitError("PROCESS_FAILURE", "git executable not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(
            "TIMEOUT", f"git {args[0]} exceeded {timeout:g}s"
        ) from exc
    except OSError as exc:
        raise GitError("PROCESS_FAILURE", f"could not run git: {exc}") from exc

    result = GitResult(
        stdout=completed.stdout.decode("utf-8", errors="replace"),
        stderr=completed.stderr.decode("utf-8", errors="replace"),
        returncode=completed.returncode,
        args=args,
    )

    if result.returncode not in ok_codes:
        message = result.stderr.strip() or f"git {args[0]} failed"
        data: dict[str, Any] = {"returncode": result.returncode}

        if "index.lock" in result.stderr or ".lock" in result.stderr:
            data["lock_conflict"] = True
            data["retries"] = len(LOCK_RETRY_DELAYS)
            found = _LOCK_PATH.search(result.stderr)

            if found:
                data["lock_path"] = found.group(1)

                try:
                    age = time.time() - os.stat(found.group(1)).st_mtime
                    data["lock_age_seconds"] = round(age, 1)
                    # Only a hint: the lock is never removed automatically.
                    data["stale_candidate"] = age > LOCK_STALE_AGE_SECONDS
                except OSError:
                    data["lock_released"] = True

            message += " (another Git process holds a lock; it was retried and never removed)"

        raise GitError("GIT_FAILURE", message, data)

    return result


def ensure_repo_root(workspace: Path) -> Path:
    """Return the repository root, requiring it to equal the workspace.

    Git tools only operate on a workspace that *is* a repository or
    worktree root, so a workspace nested inside another repository can
    never read or change the outer one.
    """

    try:
        top = run_git(workspace, ["rev-parse", "--show-toplevel"]).stdout
    except GitError as exc:
        if "not a git repository" in exc.message.lower():
            raise GitError(
                "GIT_FAILURE", "Workspace is not inside a Git repository"
            ) from exc

        raise

    root = Path(top.strip())

    if not root.exists() or not os.path.samefile(root, workspace):
        raise GitError(
            "GIT_FAILURE",
            "Workspace is not the root of a Git repository "
            f"(repository root is {root}).",
            {"repository_root": str(root)},
        )

    return workspace


def git_dir(workspace: Path) -> Path:
    out = run_git(workspace, ["rev-parse", "--absolute-git-dir"]).stdout
    return Path(out.strip())


_STATE_MARKERS = (
    ("MERGE_HEAD", "merge"),
    ("rebase-merge", "rebase"),
    ("rebase-apply", "rebase"),
    ("CHERRY_PICK_HEAD", "cherry-pick"),
    ("REVERT_HEAD", "revert"),
    ("BISECT_LOG", "bisect"),
)


def repo_state(workspace: Path) -> str | None:
    """Name of an in-progress operation (merge, rebase, ...), else None."""

    directory = git_dir(workspace)

    for marker, name in _STATE_MARKERS:
        if (directory / marker).exists():
            return name

    return None


def head_sha(workspace: Path) -> str | None:
    """Current commit, or None for a repository with no commits yet."""

    result = run_git(
        workspace, ["rev-parse", "--verify", "-q", "HEAD"], ok_codes=(0, 1)
    )
    return result.stdout.strip() or None


def resolve_commit(workspace: Path, ref: str) -> str:
    """Resolve a user-supplied ref to a full commit hash (no option injection)."""

    if not ref or ref.startswith("-") or "\0" in ref:
        raise GitError("INVALID_ARGUMENTS", f"Invalid ref: {ref!r}")

    result = run_git(
        workspace,
        ["rev-parse", "--verify", "-q", "--end-of-options", f"{ref}^{{commit}}"],
        ok_codes=(0, 1),
    )
    sha = result.stdout.strip()

    if not sha:
        raise GitError("INVALID_ARGUMENTS", f"Ref does not name a commit: {ref}")

    return sha


def clean_relative_paths(paths: list[str]) -> list[str]:
    """Validate workspace-relative path strings (no absolute, no '..')."""

    cleaned = []

    for raw in paths:
        if not isinstance(raw, str) or not raw.strip() or "\0" in raw:
            raise GitError("INVALID_ARGUMENTS", f"Invalid path: {raw!r}")

        problem = raw_path_problem(raw)

        if problem:
            raise GitError("INVALID_ARGUMENTS", f"Invalid path {raw!r}: {problem}")

        normalized = raw.replace("\\", "/")

        if (
            normalized.startswith("/")
            or (len(normalized) > 1 and normalized[1] == ":")
            or ".." in normalized.split("/")
        ):
            raise GitError(
                "PERMISSION_DENIED", f"Path must stay inside the repo: {raw}"
            )

        cleaned.append(normalized.removeprefix("./"))

    return cleaned
