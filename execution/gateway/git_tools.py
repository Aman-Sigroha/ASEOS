from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from execution.gateway.capabilities import (
    CAP_FS_READ_SECRETS,
    CAP_GIT_DISCARD,
    CAP_GIT_READ,
    CAP_GIT_WRITE,
)
from execution.security.levels import ExecutionLevel
from execution.gateway.tool import (
    Tool,
    ToolContext,
    ToolError,
    ToolOutput,
    ToolSpec,
    ensure_writable_path,
    resolve_in_workspace,
)
from execution.git.diff import DEFAULT_MAX_DIFF_BYTES, get_diff
from execution.git.info import get_info
from execution.git.mutate import (
    commit_paths,
    create_branch,
    discard_paths,
    stage_paths,
)
from execution.git.repo import GitError
from execution.git.status import get_status


class _Args(BaseModel):
    model_config = ConfigDict(extra="ignore")


def _call(function: Callable[[], Any]) -> Any:
    try:
        return function()
    except GitError as exc:
        raise ToolError(exc.category, exc.message, data=exc.data) from exc  # type: ignore[arg-type]


def _check_write_paths(context: ToolContext, paths: list[str]) -> None:
    """Refuse to stage protected files (.git, .env) or paths outside the repo."""

    for path in paths:
        target = resolve_in_workspace(context.workspace, path)
        ensure_writable_path(context.workspace, target)


# ----------------------------------------------------------- read-only


class GitStatusArgs(_Args):
    max_entries: int = Field(default=200, ge=1, le=500)


class GitStatusTool(Tool):
    spec = ToolSpec(
        name="git.status",
        level=ExecutionLevel.READ_ONLY,
        action_type="GIT_STATUS",
        description=(
            "Branch, upstream, staged/unstaged/untracked/conflicted paths "
            "and in-progress merge/rebase state."
        ),
        args_model=GitStatusArgs,
        capabilities=frozenset({CAP_GIT_READ}),
        read_only=True,
        idempotent=True,
        timeout_seconds=30,
    )

    def run(self, context: ToolContext, args: GitStatusArgs) -> ToolOutput:
        status = _call(lambda: get_status(context.workspace, args.max_entries))
        counts = status["counts"]
        where = status["branch"] or f"detached at {str(status['head_sha'])[:8]}"
        lines = [
            f"branch: {where}",
            f"clean: {status['clean']}",
            f"staged: {counts['staged']}  unstaged: {counts['unstaged']}  "
            f"untracked: {counts['untracked']}  conflicted: {counts['conflicted']}",
        ]

        if status["state"]:
            lines.append(f"in progress: {status['state']}")

        return ToolOutput(
            stdout="\n".join(lines),
            data=status,
            truncated=status["truncated"],
        )


class GitDiffArgs(_Args):
    mode: Literal["unstaged", "staged", "base"] = "unstaged"
    base: str | None = None
    paths: list[str] | None = None
    include_untracked: bool = True
    max_bytes: int = Field(default=DEFAULT_MAX_DIFF_BYTES, ge=1, le=1_000_000)
    context: int = Field(default=3, ge=0, le=20)


class GitDiffTool(Tool):
    spec = ToolSpec(
        name="git.diff",
        level=ExecutionLevel.READ_ONLY,
        action_type="GIT_DIFF",
        description=(
            "Diff with an explicit base: mode 'unstaged' (working tree vs "
            "index), 'staged' (index vs HEAD) or 'base' (working tree vs a "
            "commit/branch given in `base`). Untracked files appear as added "
            "files unless include_untracked is false. Returns the diff text "
            "plus per-file status and statistics."
        ),
        args_model=GitDiffArgs,
        capabilities=frozenset({CAP_GIT_READ}),
        read_only=True,
        idempotent=True,
        timeout_seconds=60,
    )

    def run(self, context: ToolContext, args: GitDiffArgs) -> ToolOutput:
        result = _call(
            lambda: get_diff(
                context.workspace,
                mode=args.mode,
                base=args.base,
                paths=args.paths,
                include_untracked=args.include_untracked,
                max_bytes=args.max_bytes,
                context=args.context,
                allow_secrets=CAP_FS_READ_SECRETS in context.capabilities,
            )
        )
        data = {k: v for k, v in result.items() if k != "diff"}
        output = ToolOutput(
            stdout=result["diff"], truncated=result["truncated"], data=data
        )

        if result["truncated"] and context.artifacts.enabled:
            # The inline diff was cut: keep the complete one retrievable.
            full = _call(
                lambda: get_diff(
                    context.workspace,
                    mode=args.mode,
                    base=args.base,
                    paths=args.paths,
                    include_untracked=args.include_untracked,
                    max_bytes=context.artifacts.max_artifact_bytes,
                    context=args.context,
                    allow_secrets=CAP_FS_READ_SECRETS in context.capabilities,
                )
            )
            ref = context.artifacts.put(
                full["diff"],
                kind="diff",
                description=f"full {args.mode} diff ({full['stats']['files']} files)",
                media_type="text/x-diff",
                redact=False,  # workspace content; secret files are already withheld
            )

            if ref is not None:
                output.artifacts.append(ref)

        return output


class GitInfoArgs(_Args):
    max_branches: int = Field(default=50, ge=1, le=100)
    max_commits: int = Field(default=5, ge=0, le=20)


class GitInfoTool(Tool):
    spec = ToolSpec(
        name="git.info",
        level=ExecutionLevel.READ_ONLY,
        action_type="GIT_INFO",
        description=(
            "Repository identity (root, linked-worktree flag), HEAD, current "
            "and default branch, branch list, recent commits and the Git "
            "config relevant to committing."
        ),
        args_model=GitInfoArgs,
        capabilities=frozenset({CAP_GIT_READ}),
        read_only=True,
        idempotent=True,
        timeout_seconds=30,
    )

    def run(self, context: ToolContext, args: GitInfoArgs) -> ToolOutput:
        info = _call(
            lambda: get_info(
                context.workspace, args.max_branches, args.max_commits
            )
        )
        lines = [
            f"root: {info['repository_root']}",
            f"branch: {info['branch'] or '(detached)'}  "
            f"default: {info['default_branch']}",
            f"head: {info['head_sha']}",
            f"linked worktree: {info['is_linked_worktree']}",
        ]
        return ToolOutput(stdout="\n".join(lines), data=info)


# ----------------------------------------------------------- mutating


def _paths_validator(value: list[str]) -> list[str]:
    if not value:
        raise ValueError("must name at least one file")

    return value


class GitStageArgs(_Args):
    paths: list[str]
    expected_sha256: dict[str, str | None] | None = None

    _check_paths = field_validator("paths")(_paths_validator)


class GitStageTool(Tool):
    spec = ToolSpec(
        name="git.stage",
        level=ExecutionLevel.LOW_RISK_MUTATION,
        action_type="GIT_STAGE",
        description=(
            "Stage exactly the named files (no directories, globs or '.'). "
            "Optional expected_sha256 {path: hash} refuses to stage a file "
            "that changed since you produced it."
        ),
        args_model=GitStageArgs,
        capabilities=frozenset({CAP_GIT_WRITE}),
        read_only=False,
        idempotent=True,
        timeout_seconds=90,
    )

    def run(self, context: ToolContext, args: GitStageArgs) -> ToolOutput:
        _check_write_paths(context, args.paths)
        data = _call(
            lambda: stage_paths(
                context.workspace, args.paths, args.expected_sha256
            )
        )
        return ToolOutput(
            stdout=f"Staged {len(data['staged_paths'])} file(s)", data=data
        )


class GitCommitArgs(_Args):
    message: str
    paths: list[str]

    _check_paths = field_validator("paths")(_paths_validator)


class GitCommitTool(Tool):
    spec = ToolSpec(
        name="git.commit",
        level=ExecutionLevel.LOW_RISK_MUTATION,
        action_type="GIT_COMMIT",
        description=(
            "Commit exactly the named files. Fails if the index holds any "
            "other staged change, if HEAD is detached, or during a "
            "merge/rebase. Existing hooks and signing settings apply."
        ),
        args_model=GitCommitArgs,
        capabilities=frozenset({CAP_GIT_WRITE}),
        read_only=False,
        timeout_seconds=90,
    )

    def run(self, context: ToolContext, args: GitCommitArgs) -> ToolOutput:
        data = _call(
            lambda: commit_paths(context.workspace, args.message, args.paths)
        )
        return ToolOutput(
            stdout=f"Committed {data['commit'][:12]} on {data['branch']}: "
            f"{data['subject']}",
            data=data,
        )


class GitBranchArgs(_Args):
    name: str
    start_point: str | None = None


class GitBranchTool(Tool):
    spec = ToolSpec(
        name="git.branch_create",
        level=ExecutionLevel.LOW_RISK_MUTATION,
        action_type="GIT_BRANCH",
        description=(
            "Create a new branch at HEAD or `start_point`. Does not check "
            "it out and never moves an existing branch."
        ),
        args_model=GitBranchArgs,
        capabilities=frozenset({CAP_GIT_WRITE}),
        read_only=False,
        timeout_seconds=90,
    )

    def run(self, context: ToolContext, args: GitBranchArgs) -> ToolOutput:
        data = _call(
            lambda: create_branch(
                context.workspace, args.name, args.start_point
            )
        )
        return ToolOutput(
            stdout=f"Created branch {data['branch']} at {data['commit'][:12]}",
            data=data,
        )


class GitDiscardArgs(_Args):
    paths: list[str]

    _check_paths = field_validator("paths")(_paths_validator)


class GitDiscardTool(Tool):
    spec = ToolSpec(
        name="git.discard",
        level=ExecutionLevel.ELEVATED,
        action_type="GIT_DISCARD",
        description=(
            "Discard unstaged edits to the named tracked files. Refuses "
            "untracked or staged files. Returns the discarded diff so it "
            "can be re-applied with patch.apply."
        ),
        args_model=GitDiscardArgs,
        capabilities=frozenset({CAP_GIT_DISCARD}),
        read_only=False,
        timeout_seconds=90,
    )

    def run(self, context: ToolContext, args: GitDiscardArgs) -> ToolOutput:
        _check_write_paths(context, args.paths)
        data = _call(lambda: discard_paths(context.workspace, args.paths))
        return ToolOutput(
            stdout=f"Discarded changes to {len(data['discarded_paths'])} file(s)",
            data=data,
        )
