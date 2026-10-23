"""Execution records: the audit trail of what an action did.

One compact record per meaningful (state-changing or process-running) action,
stored as an `audit` artifact and offered to observers. It is keyed by the
runtime's `action_id`, so the runtime's event stream stays the one task
history: events say *that* an action ran, the record (found through the same
id) says *what exactly* ran, under which limits, and where its outputs are.
Arguments are summarized, never copied wholesale, and run through redaction.
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from execution.artifacts.redact import redact_text

MAX_SCALAR = 200
# Arguments that carry file content or patches: only their size and hash are recorded.
CONTENT_KEYS = frozenset({"patch", "content", "old", "new"})
MAX_KEYS = 30
# Result `data` keys that tie a record to other evidence.
LINK_KEYS = ("patch_id", "run_id", "status", "base_commit", "commit", "branch", "task_id")


class ExecutionRecord(BaseModel):
    record_version: int = 1
    action_id: str
    action_type: str
    tool: str | None = None
    workspace_id: str
    task_id: str | None = None
    started_at: str
    finished_at: str
    duration_ms: int = 0
    level: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    sandboxed: bool | None = None
    success: bool
    error_category: str | None = None
    exit_code: int | None = None
    truncated: bool = False
    args_sha256: str
    args: dict[str, Any] = Field(default_factory=dict)
    links: dict[str, Any] = Field(default_factory=dict)
    artifact_ids: list[str] = Field(default_factory=list)
    artifact_errors: list[str] = Field(default_factory=list)
    redactions: int = 0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scalar(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) <= MAX_SCALAR:
            return redact_text(value).text

        return {"chars": len(value), "sha256": hashlib.sha256(value.encode()).hexdigest()}

    if isinstance(value, (int, float, bool)) or value is None:
        return value

    if isinstance(value, dict):
        return {"type": "object", "keys": sorted(map(str, value))[:MAX_KEYS]}

    if isinstance(value, (list, tuple)):
        return {"type": "list", "length": len(value)}

    return {"type": type(value).__name__}


def summarize_args(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """(digest of the full payload, bounded redacted summary).

    Environment values are never recorded (only their names), and long text
    such as patches is replaced by its length and hash."""

    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8", errors="replace")).hexdigest()
    summary: dict[str, Any] = {}

    for index, (key, value) in enumerate(sorted(payload.items())):
        if index >= MAX_KEYS:
            summary["..."] = f"{len(payload) - MAX_KEYS} more"
            break

        if key == "env" and isinstance(value, dict):
            summary[key] = {"names": sorted(map(str, value))[:MAX_KEYS]}
        elif key in CONTENT_KEYS and isinstance(value, str):
            summary[key] = {"chars": len(value), "sha256": hashlib.sha256(value.encode()).hexdigest()}
        else:
            summary[key] = _scalar(value)

    return digest, summary


def links_from(data: dict[str, Any]) -> dict[str, Any]:
    links = {k: data[k] for k in LINK_KEYS if isinstance(data.get(k), (str, int, bool))}
    version = data.get("workspace_version")

    if isinstance(version, dict) and version.get("fingerprint"):
        links["workspace_fingerprint"] = version["fingerprint"]

    return links
