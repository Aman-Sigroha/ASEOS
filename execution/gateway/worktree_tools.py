from pydantic import BaseModel, ConfigDict

from execution.gateway.capabilities import (
    CAP_GIT_READ,
    CAP_WORKTREE_DISCARD,
    CAP_WORKTREE_MANAGE,
)
from execution.gateway.git_tools import _call
from execution.security.levels import ExecutionLevel
from execution.gateway.tool import (
    Tool,
    ToolContext,
    ToolError,
    ToolOutput,
    ToolSpec,
)
from execution.worktrees.manager import WorktreeManager


class _Args(BaseModel):
    model_config = ConfigDict(extra="ignore")


def _manager(context: ToolContext) -> WorktreeManager:
    return _call(lambda: WorktreeManager(context.workspace))


class WorktreeCreateArgs(_Args):
    task_id: str
    base: str = "HEAD"


class WorktreeCreateTool(Tool):
    spec = ToolSpec(
        name="git.worktree.create",
        level=ExecutionLevel.LOW_RISK_MUTATION,
        action_type="WORKTREE_CREATE",
        description=(
            "Create an isolated, registered worktree for a task from a base "
            "commit (default HEAD). The main checkout is not modified; "
            "uncommitted changes in it are not visible to the task."
        ),
        args_model=WorktreeCreateArgs,
        capabilities=frozenset({CAP_WORKTREE_MANAGE}),
        read_only=False,
        timeout_seconds=180,
    )

    def run(self, context: ToolContext, args: WorktreeCreateArgs) -> ToolOutput:
        try:
            record = _call(
                lambda: _manager(context).create(
                    args.task_id, base=args.base, owner=context.action_id
                )
            )
        except ValueError as exc:
            raise ToolError("INVALID_ARGUMENTS", str(exc)) from exc

        return ToolOutput(
            stdout=f"Created worktree {record.worktree_id} at {record.path}",
            data=record.model_dump(),
        )


class WorktreeListArgs(_Args):
    include_removed: bool = False
    assess: bool = True


class WorktreeListTool(Tool):
    spec = ToolSpec(
        name="git.worktree.list",
        level=ExecutionLevel.READ_ONLY,
        action_type="WORKTREE_LIST",
        description=(
            "List managed worktrees with their live state: uncommitted "
            "changes, unmerged commits and whether cleanup is safe."
        ),
        args_model=WorktreeListArgs,
        capabilities=frozenset({CAP_GIT_READ}),
        read_only=True,
        idempotent=True,
        timeout_seconds=120,
    )

    def run(self, context: ToolContext, args: WorktreeListArgs) -> ToolOutput:
        def collect():
            manager = _manager(context)
            return [
                manager.describe(record, assess=args.assess)
                for record in manager.list(args.include_removed)
            ]

        items = _call(collect)
        lines = [
            f"{item['worktree_id']}  {item['task_id']}  {item['status']}"
            for item in items
        ]
        return ToolOutput(
            stdout="\n".join(lines) or "(no managed worktrees)",
            data={"worktrees": items},
        )


class WorktreeRemoveArgs(_Args):
    worktree_id: str
    discard_unreviewed: bool = False


class WorktreeRemoveTool(Tool):
    spec = ToolSpec(
        name="git.worktree.remove",
        level=ExecutionLevel.ELEVATED,
        action_type="WORKTREE_REMOVE",
        description=(
            "Remove a managed worktree. Refuses when it holds uncommitted "
            "changes, unmerged commits or is locked. discard_unreviewed "
            "only works if the trusted caller granted worktree.discard; "
            "discarded work is backed up first."
        ),
        args_model=WorktreeRemoveArgs,
        capabilities=frozenset({CAP_WORKTREE_MANAGE}),
        read_only=False,
        timeout_seconds=180,
    )

    def run(self, context: ToolContext, args: WorktreeRemoveArgs) -> ToolOutput:
        if args.discard_unreviewed and CAP_WORKTREE_DISCARD not in context.capabilities:
            raise ToolError(
                "PERMISSION_DENIED",
                "Discarding unreviewed work requires the 'worktree.discard' "
                "capability, which the model cannot grant itself.",
            )

        data = _call(
            lambda: _manager(context).remove(
                args.worktree_id, discard_confirmed=args.discard_unreviewed
            )
        )
        return ToolOutput(stdout=f"Removed worktree {args.worktree_id}", data=data)


class WorktreeReconcileArgs(_Args):
    pass


class WorktreeReconcileTool(Tool):
    spec = ToolSpec(
        name="git.worktree.reconcile",
        level=ExecutionLevel.LOW_RISK_MUTATION,
        action_type="WORKTREE_RECONCILE",
        description=(
            "Compare worktree records with Git and the disk (e.g. after a "
            "restart). Updates record statuses only; deletes nothing."
        ),
        args_model=WorktreeReconcileArgs,
        capabilities=frozenset({CAP_WORKTREE_MANAGE}),
        read_only=False,
        idempotent=True,
        timeout_seconds=120,
    )

    def run(self, context: ToolContext, args: WorktreeReconcileArgs) -> ToolOutput:
        report = _call(lambda: _manager(context).reconcile())
        return ToolOutput(
            stdout=(
                f"ok: {len(report['ok'])}, missing: {len(report['missing'])}, "
                f"orphan dirs: {len(report['orphan_dirs'])}, "
                f"interrupted: {len(report['interrupted'])}"
            ),
            data=report,
        )
