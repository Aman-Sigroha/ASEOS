"""Tools for reading stored artifacts: full logs, reports, patches, records.

Both tools are read-only and scoped to the gateway's own workspace; an
artifact of another task or workspace is indistinguishable from a missing one.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from execution.artifacts.store import ArtifactError, ArtifactMeta
from execution.gateway.capabilities import CAP_FS_READ
from execution.gateway.tool import Tool, ToolContext, ToolError, ToolOutput, ToolSpec
from execution.security.levels import ExecutionLevel

MAX_READ = 262_144


def _meta(meta: ArtifactMeta) -> dict[str, Any]:
    return {
        "id": meta.id,
        "kind": meta.kind,
        "description": meta.description,
        "media_type": meta.media_type,
        "size": meta.size,
        "sha256": meta.sha256,
        "original_size": meta.original_size,
        "stored_truncated": meta.stored_truncated,
        "redactions": meta.redactions,
        "retention": meta.retention,
        "created_at": meta.created_at,
        "expires_at": meta.expires_at,
        "action_id": meta.action_id,
        "tool": meta.tool,
    }


def _error(exc: ArtifactError) -> ToolError:
    category = "INVALID_ARGUMENTS" if exc.kind in ("not_found", "invalid") else "INTERNAL_ERROR"
    return ToolError(category, str(exc), data={"artifact_error": exc.kind})  # type: ignore[arg-type]


class ArtifactReadArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, max_length=64)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=65536, ge=1, le=MAX_READ)
    from_end: bool = False
    """Read the last `limit` bytes (offset is ignored) - failures usually end with the useful part."""


class ArtifactReadTool(Tool):
    spec = ToolSpec(
        name="artifact.read",
        level=ExecutionLevel.READ_ONLY,
        action_type="READ_ARTIFACT",
        description=(
            "Read part of a stored artifact (complete command output, test "
            "report, patch, full diff) by id. Bounded: use offset/limit to "
            "page, or from_end to read the tail. Returns the artifact's "
            "metadata, including whether secrets were redacted."
        ),
        args_model=ArtifactReadArgs,
        capabilities=frozenset({CAP_FS_READ}),
        read_only=True,
        idempotent=True,
        timeout_seconds=30,
    )

    def run(self, context: ToolContext, args: ArtifactReadArgs) -> ToolOutput:
        try:
            chunk, meta, eof = context.artifacts.read(
                args.id, offset=args.offset, limit=args.limit, from_end=args.from_end
            )
        except ArtifactError as exc:
            raise _error(exc) from exc

        start = max(meta.size - len(chunk), 0) if args.from_end else args.offset
        data = {
            **_meta(meta),
            "offset": start,
            "returned_bytes": len(chunk),
            "eof": eof,
            "next_offset": None if eof else start + len(chunk),
        }
        return ToolOutput(
            stdout=chunk.decode("utf-8", errors="replace"), truncated=not eof, data=data
        )


class ArtifactListArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: str | None = Field(default=None, max_length=64)
    action_id: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=20, ge=1, le=100)


class ArtifactListTool(Tool):
    spec = ToolSpec(
        name="artifact.list",
        level=ExecutionLevel.READ_ONLY,
        action_type="LIST_ARTIFACTS",
        description=(
            "List stored artifacts of this workspace, newest first, "
            "optionally by kind (command_output, test_report, patch, diff, "
            "execution_record) or by the action that produced them."
        ),
        args_model=ArtifactListArgs,
        capabilities=frozenset({CAP_FS_READ}),
        read_only=True,
        idempotent=True,
        timeout_seconds=30,
    )

    def run(self, context: ToolContext, args: ArtifactListArgs) -> ToolOutput:
        found = context.artifacts.find(
            kind=args.kind, action_id=args.action_id, limit=args.limit
        )
        lines = [f"{m.id}  {m.kind}  {m.size} bytes  {m.description}" for m in found]
        return ToolOutput(
            stdout="\n".join(lines) or "no artifacts",
            data={"artifacts": [_meta(m) for m in found], "count": len(found)},
        )
