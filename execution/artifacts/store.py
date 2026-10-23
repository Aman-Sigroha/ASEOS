"""Content store for execution artifacts: full logs, reports, patches, records.

Layout under the store root (outside the workspace, so artifacts never show
up in diffs, fingerprints or the model's file tools):

    objects/<id>.blob     the stored (redacted, size-capped) bytes
    meta/<id>.json        metadata; its presence marks the artifact committed
    spool/                scratch files for running commands (raw output)

Every artifact has an owner (workspace, optional task, action, tool), a
retention class and an integrity hash. Reads are scoped to the owning
workspace: another task's artifact looks exactly like a missing one.
"""

import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from execution.artifacts.redact import redact_text

Retention = Literal["temporary", "audit"]

_ID = re.compile(r"^art_[0-9a-f]{24}$")
DAY = 86400.0
TRUNCATION_MARKER = "\n... [{omitted} bytes omitted from the stored copy] ...\n"


class ArtifactError(Exception):
    """Store failure. `kind`: not_found | invalid | full | io | integrity."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class RetentionPolicy:
    temporary_ttl_seconds: float = 7 * DAY
    audit_ttl_seconds: float = 90 * DAY
    max_artifact_bytes: int = 20 * 1024 * 1024
    max_total_bytes: int = 500 * 1024 * 1024
    spool_ttl_seconds: float = 1 * DAY
    orphan_grace_seconds: float = 3600.0

    def ttl(self, retention: Retention) -> float:
        return self.audit_ttl_seconds if retention == "audit" else self.temporary_ttl_seconds


@dataclass(frozen=True)
class Owner:
    workspace_id: str
    task_id: str | None = None
    action_id: str | None = None
    tool: str | None = None


class ArtifactMeta(BaseModel):
    id: str
    kind: str
    description: str = ""
    media_type: str = "text/plain"
    size: int = Field(ge=0)
    """Bytes actually stored."""
    sha256: str
    """Hash of the stored bytes (what a reader receives)."""
    original_size: int = Field(ge=0)
    """Bytes offered before capping and redaction."""
    stored_truncated: bool = False
    redacted: bool = False
    """Whether pattern redaction was applied to this kind of content."""
    redactions: int = 0
    redaction_kinds: dict[str, int] = Field(default_factory=dict)
    retention: Retention = "temporary"
    created_at: str
    expires_at: str
    workspace_id: str
    task_id: str | None = None
    action_id: str | None = None
    tool: str | None = None


def workspace_id(path: Path | str) -> str:
    """Stable, non-reversible identity of a workspace for ownership checks."""

    normal = os.path.normcase(str(Path(path).resolve()))
    return hashlib.sha256(normal.encode("utf-8")).hexdigest()[:16]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")

    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _cap(data: bytes, limit: int) -> tuple[bytes, bool]:
    """Keep the head and the tail of oversized content (failures end with the useful part)."""

    if len(data) <= limit:
        return data, False

    omitted = len(data) - limit
    marker = TRUNCATION_MARKER.format(omitted=omitted).encode()
    keep = max(limit - len(marker), 2)
    head = keep // 2
    return data[:head] + marker + data[len(data) - (keep - head):], True


class ArtifactStore:
    def __init__(self, root: Path | str, policy: RetentionPolicy | None = None) -> None:
        self.root = Path(root)
        self.policy = policy or RetentionPolicy()
        self._objects = self.root / "objects"
        self._meta = self.root / "meta"
        self._spool = self.root / "spool"
        self._lock = threading.RLock()

        try:
            for directory in (self._objects, self._meta, self._spool):
                directory.mkdir(parents=True, exist_ok=True)

            if os.name != "nt":
                os.chmod(self.root, 0o700)
        except OSError as exc:
            raise ArtifactError("io", f"Cannot create the artifact store: {exc}") from exc

    # ---------------------------------------------------------------- paths

    def spool_dir(self) -> Path:
        return self._spool

    def _blob(self, artifact_id: str) -> Path:
        return self._objects / f"{artifact_id}.blob"

    def _meta_path(self, artifact_id: str) -> Path:
        return self._meta / f"{artifact_id}.json"

    @staticmethod
    def _check_id(artifact_id: str) -> None:
        if not isinstance(artifact_id, str) or not _ID.match(artifact_id):
            raise ArtifactError("invalid", "Malformed artifact id.")

    # ------------------------------------------------------------------ put

    def put(
        self,
        data: bytes | str,
        *,
        kind: str,
        owner: Owner,
        description: str = "",
        media_type: str = "text/plain",
        retention: Retention = "temporary",
        redact: bool = True,
        original_size: int | None = None,
    ) -> ArtifactMeta:
        """Store `data` (capped, then redacted when `redact`). Raises ArtifactError."""

        raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        offered = original_size if original_size is not None else len(raw)
        capped, truncated = _cap(raw, self.policy.max_artifact_bytes)
        count, kinds = 0, {}

        if redact:
            result = redact_text(capped.decode("utf-8", errors="replace"))
            count, kinds = result.count, result.kinds

            if result.changed:
                capped = result.text.encode("utf-8")

        artifact_id = f"art_{uuid.uuid4().hex[:24]}"
        created = _now()
        meta = ArtifactMeta(
            id=artifact_id,
            kind=kind,
            description=description[:300],
            media_type=media_type,
            size=len(capped),
            sha256=hashlib.sha256(capped).hexdigest(),
            original_size=offered,
            stored_truncated=truncated or offered > len(raw),
            redacted=redact,
            redactions=count,
            redaction_kinds=kinds,
            retention=retention,
            created_at=_iso(created),
            expires_at=_iso(created + timedelta(seconds=self.policy.ttl(retention))),
            workspace_id=owner.workspace_id,
            task_id=owner.task_id,
            action_id=owner.action_id,
            tool=owner.tool,
        )

        with self._lock:
            self._make_room(len(capped))

            try:
                _write_atomic(self._blob(artifact_id), capped)
                _write_atomic(self._meta_path(artifact_id), meta.model_dump_json().encode())
            except OSError as exc:
                self._blob(artifact_id).unlink(missing_ok=True)
                self._meta_path(artifact_id).unlink(missing_ok=True)
                raise ArtifactError("io", f"Cannot write the artifact: {exc}") from exc

        return meta

    def put_file(
        self,
        path: Path | str,
        *,
        kind: str,
        owner: Owner,
        description: str = "",
        media_type: str = "text/plain",
        retention: Retention = "temporary",
        redact: bool = True,
    ) -> ArtifactMeta:
        """Store a file's head and tail without loading all of it."""

        limit = self.policy.max_artifact_bytes

        try:
            size = Path(path).stat().st_size

            with open(path, "rb") as handle:
                if size <= limit:
                    data = handle.read()
                else:
                    half = limit // 2
                    head = handle.read(half)
                    handle.seek(size - half)
                    tail = handle.read(half)
                    marker = TRUNCATION_MARKER.format(omitted=size - 2 * half).encode()
                    data = head + marker + tail
        except OSError as exc:
            raise ArtifactError("io", f"Cannot read {path}: {exc}") from exc

        return self.put(
            data, kind=kind, owner=owner, description=description,
            media_type=media_type, retention=retention, redact=redact,
            original_size=size,
        )

    # ----------------------------------------------------------------- read

    def get_meta(self, artifact_id: str, workspace: str | None = None) -> ArtifactMeta:
        self._check_id(artifact_id)

        try:
            meta = ArtifactMeta.model_validate_json(self._meta_path(artifact_id).read_bytes())
        except FileNotFoundError:
            raise ArtifactError("not_found", "No such artifact.") from None
        except (OSError, ValueError) as exc:
            raise ArtifactError("io", f"Unreadable artifact metadata: {exc}") from exc

        if workspace is not None and meta.workspace_id != workspace:
            raise ArtifactError("not_found", "No such artifact.")  # indistinguishable

        if datetime.fromisoformat(meta.expires_at) <= _now():
            raise ArtifactError("not_found", "No such artifact.")

        return meta

    def read(
        self,
        artifact_id: str,
        *,
        workspace: str | None = None,
        offset: int = 0,
        limit: int = 65536,
        from_end: bool = False,
    ) -> tuple[bytes, ArtifactMeta, bool]:
        """A bounded slice: (bytes, meta, reached_end)."""

        meta = self.get_meta(artifact_id, workspace)
        limit = max(0, min(limit, 1024 * 1024))
        start = max(meta.size - limit, 0) if from_end else max(offset, 0)

        try:
            with open(self._blob(artifact_id), "rb") as handle:
                handle.seek(start)
                chunk = handle.read(limit)
        except FileNotFoundError:
            raise ArtifactError("integrity", "Artifact content is missing.") from None
        except OSError as exc:
            raise ArtifactError("io", f"Cannot read the artifact: {exc}") from exc

        return chunk, meta, start + len(chunk) >= meta.size

    def verify(self, artifact_id: str, workspace: str | None = None) -> ArtifactMeta:
        """Re-hash the stored bytes; raises ArtifactError('integrity') on mismatch."""

        meta = self.get_meta(artifact_id, workspace)

        try:
            blob = self._blob(artifact_id).read_bytes()
        except OSError as exc:
            raise ArtifactError("integrity", f"Artifact content is unreadable: {exc}") from exc

        if len(blob) != meta.size or hashlib.sha256(blob).hexdigest() != meta.sha256:
            raise ArtifactError("integrity", "Artifact content does not match its recorded hash.")

        return meta

    def find(
        self,
        *,
        workspace: str,
        kind: str | None = None,
        action_id: str | None = None,
        limit: int = 50,
    ) -> list[ArtifactMeta]:
        found: list[ArtifactMeta] = []

        for entry in self._meta.glob("art_*.json"):
            try:
                meta = self.get_meta(entry.stem, workspace)
            except ArtifactError:
                continue

            if (kind is None or meta.kind == kind) and (
                action_id is None or meta.action_id == action_id
            ):
                found.append(meta)

        found.sort(key=lambda m: m.created_at, reverse=True)
        return found[:limit]

    def delete(self, artifact_id: str) -> bool:
        self._check_id(artifact_id)

        with self._lock:
            existed = self._meta_path(artifact_id).exists()
            self._meta_path(artifact_id).unlink(missing_ok=True)
            self._blob(artifact_id).unlink(missing_ok=True)

        return existed

    # --------------------------------------------------------- housekeeping

    def total_bytes(self) -> int:
        total = 0

        for entry in self._objects.glob("art_*.blob"):
            try:
                total += entry.stat().st_size
            except OSError:
                continue

        return total

    def _make_room(self, incoming: int) -> None:
        if self.total_bytes() + incoming <= self.policy.max_total_bytes:
            return

        self.gc(reserve=incoming)

        if self.total_bytes() + incoming > self.policy.max_total_bytes:
            raise ArtifactError(
                "full",
                "The artifact store is full of artifacts that must be kept; "
                "nothing more can be stored until they expire.",
            )

    def audit(self, *, deep: bool = False) -> dict[str, Any]:
        """Report (never repair) inconsistencies: blobs without metadata,
        metadata without a blob, expired artifacts not yet collected, leftover
        spool files, and with `deep` content that no longer matches its hash."""

        metas = {p.stem for p in self._meta.glob("art_*.json")}
        blobs = {p.stem for p in self._objects.glob("art_*.blob")}
        now = _now()
        expired = 0
        corrupt: list[str] = []

        for artifact_id in sorted(metas):
            try:
                meta = ArtifactMeta.model_validate_json(self._meta_path(artifact_id).read_bytes())
            except (OSError, ValueError):
                corrupt.append(artifact_id)
                continue

            if datetime.fromisoformat(meta.expires_at) <= now:
                expired += 1
            elif deep and artifact_id in blobs:
                try:
                    self.verify(artifact_id)
                except ArtifactError:
                    corrupt.append(artifact_id)

        return {
            "artifacts": len(metas),
            "orphan_blobs": sorted(blobs - metas),
            "missing_blobs": sorted(metas - blobs),
            "corrupt": corrupt,
            "expired_uncollected": expired,
            "spool_files": sorted(p.name for p in self._spool.iterdir()),
            "total_bytes": self.total_bytes(),
            "clean": not (blobs - metas or metas - blobs or corrupt),
        }

    def gc(self, now: float | None = None, reserve: int = 0) -> dict[str, Any]:
        """Remove expired artifacts, orphans and stale spool files; then
        enforce the size quota by dropping the oldest *temporary* artifacts.
        Audit artifacts are only ever removed when they expire. `reserve`
        is room to leave free for an artifact about to be written."""

        now_ts = now if now is not None else time.time()
        report = {"expired": 0, "orphans": 0, "spool": 0, "quota": 0, "freed_bytes": 0}

        with self._lock:
            live: list[tuple[str, ArtifactMeta]] = []

            for entry in list(self._meta.glob("art_*.json")):
                try:
                    meta = ArtifactMeta.model_validate_json(entry.read_bytes())
                except (OSError, ValueError):
                    continue

                if datetime.fromisoformat(meta.expires_at).timestamp() <= now_ts:
                    report["freed_bytes"] += meta.size
                    self.delete(meta.id)
                    report["expired"] += 1
                else:
                    live.append((meta.created_at, meta))

            known = {meta.id for _, meta in live}

            for blob in list(self._objects.glob("art_*.blob")):
                age = now_ts - blob.stat().st_mtime

                if blob.stem not in known and age > self.policy.orphan_grace_seconds:
                    report["freed_bytes"] += blob.stat().st_size
                    blob.unlink(missing_ok=True)
                    report["orphans"] += 1

            for scratch in list(self._spool.iterdir()):
                try:
                    if now_ts - scratch.stat().st_mtime > self.policy.spool_ttl_seconds:
                        report["freed_bytes"] += scratch.stat().st_size
                        scratch.unlink(missing_ok=True)
                        report["spool"] += 1
                except OSError:
                    continue

            excess = self.total_bytes() + reserve - self.policy.max_total_bytes

            for _, meta in sorted(live, key=lambda item: item[0]):
                if excess <= 0:
                    break

                if meta.retention == "temporary" and self._meta_path(meta.id).exists():
                    self.delete(meta.id)
                    excess -= meta.size
                    report["freed_bytes"] += meta.size
                    report["quota"] += 1

        return report


def default_store_for(workspace: Path | str, policy: RetentionPolicy | None = None) -> ArtifactStore:
    """Per-repository store inside the git common directory; for a plain
    directory, a per-workspace folder under the system temp directory."""

    from execution.git.repo import GitError, run_git

    path = Path(workspace).resolve()

    try:
        common = run_git(path, ["rev-parse", "--git-common-dir"]).stdout.strip()
        return ArtifactStore((path / common).resolve() / "aseos" / "artifacts", policy)
    except (GitError, ArtifactError, OSError):
        pass

    return ArtifactStore(
        Path(tempfile.gettempdir()) / "aseos-artifacts" / workspace_id(path), policy
    )
