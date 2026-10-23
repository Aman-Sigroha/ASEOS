"""The per-action handle a tool uses to store artifacts.

A sink never raises: storing evidence must not turn a successful operation
into a failed one. Failures are recorded on the sink and surfaced in the
result (`data["artifact_errors"]`), and the bounded excerpt in the result
remains the evidence of last resort.
"""

import os
from pathlib import Path
from typing import Any

from execution.artifacts.store import (
    ArtifactError,
    ArtifactMeta,
    ArtifactStore,
    Owner,
    Retention,
)
from runtime.schemas.execution import ArtifactRef


def to_ref(meta: ArtifactMeta) -> ArtifactRef:
    return ArtifactRef(
        id=meta.id,
        kind=meta.kind,
        description=meta.description,
        size=meta.size,
        sha256=meta.sha256,
        retention=meta.retention,
        truncated=meta.stored_truncated,
        redactions=meta.redactions,
    )


class ArtifactSink:
    """Artifacts of one action, owned by one workspace (and task)."""

    enabled = True

    def __init__(
        self,
        store: ArtifactStore,
        *,
        workspace: str,
        action_id: str = "",
        tool: str | None = None,
        task_id: str | None = None,
        extra_secrets: tuple[str, ...] = (),
    ) -> None:
        self.extra_secrets = extra_secrets
        self.store = store
        self.workspace = workspace
        self.owner = Owner(workspace, task_id, action_id or None, tool)
        self.refs: list[ArtifactRef] = []
        self.errors: list[str] = []

    @property
    def spool_dir(self) -> Path:
        return self.store.spool_dir()

    @property
    def max_artifact_bytes(self) -> int:
        return self.store.policy.max_artifact_bytes

    def _record(self, meta: ArtifactMeta) -> ArtifactRef:
        ref = to_ref(meta)
        self.refs.append(ref)
        return ref

    def put(
        self,
        data: bytes | str,
        *,
        kind: str,
        description: str = "",
        media_type: str = "text/plain",
        retention: Retention = "temporary",
        redact: bool = True,
    ) -> ArtifactRef | None:
        try:
            return self._record(
                self.store.put(
                    data, kind=kind, owner=self.owner, description=description,
                    media_type=media_type, retention=retention, redact=redact,
                    extra_secrets=self.extra_secrets,
                )
            )
        except ArtifactError as exc:
            self.errors.append(f"{kind}: {exc}")
        except Exception as exc:  # the store must never break an action
            self.errors.append(f"{kind}: {type(exc).__name__}: {exc}")

        return None

    def put_file(
        self,
        path: Path | str,
        *,
        kind: str,
        description: str = "",
        media_type: str = "text/plain",
        retention: Retention = "temporary",
        redact: bool = True,
        remove_source: bool = False,
    ) -> ArtifactRef | None:
        """Store a file; with `remove_source` the original is deleted whether
        or not storing worked (raw command output must not linger on disk)."""

        try:
            return self._record(
                self.store.put_file(
                    path, kind=kind, owner=self.owner, description=description,
                    media_type=media_type, retention=retention, redact=redact,
                    extra_secrets=self.extra_secrets,
                )
            )
        except ArtifactError as exc:
            self.errors.append(f"{kind}: {exc}")
        except Exception as exc:
            self.errors.append(f"{kind}: {type(exc).__name__}: {exc}")
        finally:
            if remove_source:
                try:
                    os.unlink(path)
                except OSError:
                    pass

        return None

    # Reads are scoped to this workspace by construction.

    def read(self, artifact_id: str, **kwargs: Any) -> tuple[bytes, ArtifactMeta, bool]:
        return self.store.read(artifact_id, workspace=self.workspace, **kwargs)

    def find(self, **kwargs: Any) -> list[ArtifactMeta]:
        return self.store.find(workspace=self.workspace, **kwargs)

    def verify(self, artifact_id: str) -> ArtifactMeta:
        return self.store.verify(artifact_id, workspace=self.workspace)

    def failure_data(self) -> dict[str, Any]:
        return {"artifact_errors": list(self.errors)} if self.errors else {}


class NullSink:
    """Used when artifacts are disabled or the store could not be opened."""

    enabled = False
    spool_dir = None
    max_artifact_bytes = 0
    refs: list[ArtifactRef] = []
    errors: list[str] = []

    def put(self, *args: Any, **kwargs: Any) -> None:
        return None

    def put_file(self, *args: Any, **kwargs: Any) -> None:
        return None

    def read(self, *args: Any, **kwargs: Any):
        raise ArtifactError("not_found", "Artifacts are not enabled for this gateway.")

    def find(self, **kwargs: Any) -> list[ArtifactMeta]:
        return []

    def verify(self, artifact_id: str):
        raise ArtifactError("not_found", "Artifacts are not enabled for this gateway.")

    def failure_data(self) -> dict[str, Any]:
        return {}
