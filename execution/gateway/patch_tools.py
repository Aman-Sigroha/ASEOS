import json

from pydantic import BaseModel, ConfigDict, Field, field_validator

from execution.gateway.capabilities import (
    CAP_FS_DELETE,
    CAP_FS_READ,
    CAP_FS_WRITE,
)
from execution.gateway.tool import (
    Tool,
    ToolContext,
    ToolOutput,
    ToolSpec,
)
from execution.patches.engine import apply_plan, describe_plan, plan_patch


class _PatchArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    patch: str = Field(min_length=1)
    base: dict[str, str] | None = None
    reverse: bool = False

    @field_validator("base")
    @classmethod
    def _check_base(cls, value):
        for path, digest in (value or {}).items():
            if len(digest) != 64 or any(
                c not in "0123456789abcdefABCDEF" for c in digest
            ):
                raise ValueError(f"base[{path!r}] must be a sha256 hex digest")

        return value


class PatchValidateTool(Tool):
    spec = ToolSpec(
        name="patch.validate",
        action_type="VALIDATE_PATCH",
        description=(
            "Check a unified diff against the workspace without changing "
            "anything: format, paths, base versions and hunk context."
        ),
        args_model=_PatchArgs,
        capabilities=frozenset({CAP_FS_READ}),
        read_only=True,
        idempotent=True,
        timeout_seconds=30,
    )

    def run(self, context: ToolContext, args: _PatchArgs) -> ToolOutput:
        plan = plan_patch(
            context.workspace,
            args.patch,
            base=args.base,
            reverse=args.reverse,
            allow_delete=CAP_FS_DELETE in context.capabilities,
        )
        data = describe_plan(plan)
        data["action_id"] = context.action_id
        summary = ", ".join(f"{f['action']} {f['path']}" for f in data["files"])
        return ToolOutput(stdout=f"Patch is valid: {summary}", data=data)


class PatchApplyTool(Tool):
    """Apply a unified diff to one or more files.

    All files are validated before anything is written. Writes are atomic
    per file; if a later file fails, earlier ones are restored (best-effort
    rollback, reported in `data`). Deleting a file needs `fs.delete`.
    """

    spec = ToolSpec(
        name="patch.apply",
        action_type="APPLY_PATCH",
        description=(
            "Apply a unified diff (create/modify/delete, multiple files). "
            "Reports the diff actually produced; reverse=true undoes a "
            "previously applied diff."
        ),
        args_model=_PatchArgs,
        capabilities=frozenset({CAP_FS_WRITE}),
        read_only=False,
        timeout_seconds=60,
    )

    def run(self, context: ToolContext, args: _PatchArgs) -> ToolOutput:
        plan = plan_patch(
            context.workspace,
            args.patch,
            base=args.base,
            reverse=args.reverse,
            allow_delete=CAP_FS_DELETE in context.capabilities,
        )
        data = apply_plan(plan, action_id=context.action_id)
        summary = ", ".join(f"{f['action']} {f['path']}" for f in data["files"])
        return ToolOutput(
            stdout=(
                f"Applied patch {data['patch_id'][:12]}: {summary} "
                f"(+{data['additions']} -{data['deletions']})"
            ),
            data=json.loads(json.dumps(data)),
            truncated=data["applied_diff_truncated"],
        )
