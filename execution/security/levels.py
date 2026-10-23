"""Risk-based execution levels and the capability profiles built on them.

Every tool declares the level it needs. A gateway is built for a profile
and refuses any tool above the profile's level, *in addition to* the
capability check. Both live in trusted code: nothing in an action payload
can change either one.

| Level               | Meaning                                         |
| ------------------- | ----------------------------------------------- |
| READ_ONLY           | inspect files, Git state, worktrees             |
| LOW_RISK_MUTATION   | validated, auditable writes (patches, staging)  |
| DEV_EXECUTION       | run approved tests / builds / linters           |
| ELEVATED            | destructive or wide-reaching operations         |
"""

from dataclasses import dataclass
from enum import IntEnum

FS_READ = "fs.read"
FS_WRITE = "fs.write"
FS_DELETE = "fs.delete"
FS_READ_SECRETS = "fs.read_secrets"
GIT_READ = "git.read"
GIT_WRITE = "git.write"
GIT_DISCARD = "git.discard"
WORKTREE_MANAGE = "worktree.manage"
WORKTREE_DISCARD = "worktree.discard"
TEST_RUN = "test.run"
SHELL_RUN = "shell.run"
SHELL_NETWORK = "shell.network"


class ExecutionLevel(IntEnum):
    READ_ONLY = 0
    LOW_RISK_MUTATION = 1
    DEV_EXECUTION = 2
    ELEVATED = 3


@dataclass(frozen=True)
class Profile:
    name: str
    max_level: ExecutionLevel
    capabilities: frozenset[str]
    # Commands must run inside a sandbox; if none is available they are
    # refused rather than run on the host.
    sandbox_required: bool = False
    description: str = ""


READ_ONLY = Profile(
    "read_only",
    ExecutionLevel.READ_ONLY,
    frozenset({FS_READ, GIT_READ}),
    description="Inspect files and Git state. Cannot change anything.",
)

WORKTREE_EDIT = Profile(
    "worktree_edit",
    ExecutionLevel.LOW_RISK_MUTATION,
    frozenset({FS_READ, FS_WRITE, GIT_READ, GIT_WRITE, WORKTREE_MANAGE}),
    description=(
        "Edit, stage and commit in an isolated worktree. Cannot run "
        "programs, delete files or discard work."
    ),
)

DEV = Profile(
    "dev",
    ExecutionLevel.DEV_EXECUTION,
    frozenset(
        {FS_READ, FS_WRITE, GIT_READ, GIT_WRITE, WORKTREE_MANAGE, TEST_RUN, SHELL_RUN}
    ),
    sandbox_required=True,
    description=(
        "Everything in worktree_edit plus approved tests/builds/linters. "
        "Commands only run inside a sandbox; with none available they are "
        "refused."
    ),
)

ELEVATED = Profile(
    "elevated",
    ExecutionLevel.ELEVATED,
    frozenset(
        {
            FS_READ, FS_WRITE, FS_DELETE, GIT_READ, GIT_WRITE, GIT_DISCARD,
            WORKTREE_MANAGE, WORKTREE_DISCARD, TEST_RUN, SHELL_RUN,
            SHELL_NETWORK,
        }
    ),
    sandbox_required=True,
    description=(
        "Adds deletion, discarding work and installs. Needs explicit "
        "approval; never selected by a model request."
    ),
)

PROFILES: dict[str, Profile] = {
    p.name: p for p in (READ_ONLY, WORKTREE_EDIT, DEV, ELEVATED)
}
