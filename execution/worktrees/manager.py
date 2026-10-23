"""Task-isolated Git worktrees with a recoverable lifecycle.

Rules this module enforces:
- The main checkout is never modified: a worktree is created from a base
  *commit*, so uncommitted edits in the main checkout are neither visible
  to nor touched by the task.
- One live worktree per task id; a directory or branch that already exists
  without a matching record is refused, never reused or deleted.
- Removal only ever targets a path recorded for that worktree that sits
  directly inside the managed root. Dirty trees, unmerged commits and
  locked worktrees are refused unless the *trusted caller* passes
  `discard_confirmed=True`, and even then a patch/ref backup is written.
- All registry and Git-administration changes are serialized by a
  cross-process lock, and a status of "creating"/"removing" is persisted
  before the risky step so a crash can be reconciled afterwards.
"""

import hashlib
import os
import time
import uuid
from pathlib import Path
from typing import Any

from execution.git.diff import get_diff
from execution.git.repo import (
    GitError,
    ensure_repo_root,
    head_sha,
    resolve_commit,
    run_git,
)
from execution.git.status import get_status
from execution.patches.fileio import atomic_write_bytes
from execution.worktrees.ids import (
    WORKTREE_ROOT,
    branch_for,
    validate_task_id,
)
from execution.worktrees.record import (
    LIVE_STATUSES,
    WorktreeRecord,
    WorktreeStatus,
    utc_now,
)
from execution.worktrees.store import WorktreeStore

MAX_BACKUP_BYTES = 20_000_000
_GIT_RETRIES = 3


def _norm(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


class WorktreeManager:
    def __init__(
        self, repo_path: str | Path, managed_root: str | Path | None = None
    ) -> None:
        self.repo = Path(repo_path).resolve()
        ensure_repo_root(self.repo)
        directory = run_git(self.repo, ["rev-parse", "--absolute-git-dir"]).stdout
        common = run_git(self.repo, ["rev-parse", "--git-common-dir"]).stdout

        self.git_dir = Path(directory.strip())
        self.common_dir = (self.repo / common.strip()).resolve()

        if not os.path.samefile(self.git_dir, self.common_dir):
            raise GitError(
                "PERMISSION_DENIED",
                "Worktrees can only be managed from the main checkout, "
                "not from another worktree.",
            )

        self.root = (
            Path(managed_root).resolve()
            if managed_root is not None
            else (self.repo / WORKTREE_ROOT).resolve()
        )
        self.store = WorktreeStore(self.common_dir / "aseos")
        self.repo_id = hashlib.sha256(
            _norm(self.common_dir).encode("utf-8")
        ).hexdigest()[:16]

    # ---------------------------------------------------------- helpers

    def _git(self, args: list[str], *, read_only: bool = True, **kw):
        """Run git, retrying briefly when another Git process holds a lock."""

        for attempt in range(_GIT_RETRIES):
            try:
                return run_git(self.repo, args, read_only=read_only, **kw)
            except GitError as exc:
                if not exc.data.get("lock_conflict") or attempt == _GIT_RETRIES - 1:
                    raise

                time.sleep(0.2 * (attempt + 1))

    def managed_dir(self, task_id: str) -> Path:
        target = (self.root / validate_task_id(task_id)).resolve()

        if target.parent != self.root:
            raise GitError(
                "PERMISSION_DENIED", "Worktree path escapes the managed root"
            )

        return target

    def _ensure_excluded(self) -> None:
        """Keep the managed root out of the main checkout's `git status`."""

        try:
            relative = self.root.relative_to(self.repo)
        except ValueError:
            return

        exclude = self.common_dir / "info" / "exclude"
        line = f"/{relative.as_posix()}/"
        current = exclude.read_text("utf-8") if exclude.is_file() else ""

        if line in current.splitlines():
            return

        exclude.parent.mkdir(parents=True, exist_ok=True)
        separator = "" if not current or current.endswith("\n") else "\n"
        atomic_write_bytes(
            exclude, (current + separator + line + "\n").encode("utf-8")
        )

    def _git_worktrees(self) -> list[dict[str, Any]]:
        out = self._git(["worktree", "list", "--porcelain"]).stdout
        entries: list[dict[str, Any]] = []

        for block in out.split("\n\n"):
            entry: dict[str, Any] = {}

            for line in block.splitlines():
                key, _, value = line.partition(" ")

                if key == "worktree":
                    entry["path"] = value
                elif key == "HEAD":
                    entry["head"] = value
                elif key == "branch":
                    entry["branch"] = value.removeprefix("refs/heads/")
                elif key == "locked":
                    entry["locked"] = value or True
                elif key == "prunable":
                    entry["prunable"] = value or True

            if "path" in entry:
                entries.append(entry)

        return entries

    def _branch_exists(self, branch: str) -> bool:
        return (
            self._git(
                ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
                ok_codes=(0, 1),
            ).returncode
            == 0
        )

    # ------------------------------------------------------------ query

    def get(self, worktree_id: str) -> WorktreeRecord:
        record = self.store.load(worktree_id)

        if record is None:
            raise GitError(
                "INVALID_ARGUMENTS", f"Unknown worktree id: {worktree_id}"
            )

        return record

    def list(self, include_removed: bool = False) -> list[WorktreeRecord]:
        records, _ = self.store.list()
        return [
            r for r in records if include_removed or r.status != "removed"
        ]

    def find_live(self, task_id: str) -> WorktreeRecord | None:
        for record in self.list():
            if record.task_id == task_id:
                return record

        return None

    # ----------------------------------------------------------- create

    def create(
        self,
        task_id: str,
        *,
        base: str = "HEAD",
        owner: str | None = None,
    ) -> WorktreeRecord:
        validate_task_id(task_id)

        with self.store.lock():
            existing = self.find_live(task_id)

            if existing is not None:
                raise GitError(
                    "GIT_FAILURE",
                    f"Task '{task_id}' already has a worktree "
                    f"({existing.worktree_id}, status {existing.status}).",
                    {"worktree_id": existing.worktree_id},
                )

            base_commit = resolve_commit(self.repo, base)
            path = self.managed_dir(task_id)
            branch = branch_for(task_id)

            if path.exists():
                raise GitError(
                    "GIT_FAILURE",
                    f"{path} already exists but has no worktree record; "
                    "refusing to reuse or delete it.",
                    {"path": str(path)},
                )

            if self._branch_exists(branch):
                raise GitError(
                    "GIT_FAILURE",
                    f"Branch '{branch}' already exists; refusing to reuse it. "
                    "Use a different task id or resolve the branch first.",
                    {"branch": branch},
                )

            self._ensure_excluded()
            record = WorktreeRecord(
                worktree_id=f"wt-{uuid.uuid4().hex[:12]}",
                task_id=task_id,
                repo_id=self.repo_id,
                repo_root=str(self.repo),
                path=str(path),
                branch=branch,
                base_ref=base,
                base_commit=base_commit,
                owner=owner or "",
                status="creating",
            )
            self.store.save(record)  # persisted *before* the Git step

            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                self._git(
                    ["worktree", "add", "-b", branch, str(path), base_commit],
                    read_only=False,
                    timeout=120,
                )

                if head_sha(path) != base_commit:
                    raise GitError(
                        "INTERNAL_ERROR",
                        "New worktree is not at the requested base commit.",
                    )
            except Exception:
                self.store.delete(record.worktree_id)
                raise

            return self._set(record, "active")

    # ---------------------------------------------------------- status

    def _set(
        self, record: WorktreeRecord, status: WorktreeStatus, note: str = ""
    ) -> WorktreeRecord:
        record.status = status
        record.updated_at = utc_now()

        if note:
            record.note = note

        if status == "removed":
            record.removed_at = record.updated_at

        self.store.save(record)
        return record

    def mark(
        self, worktree_id: str, status: WorktreeStatus, note: str = ""
    ) -> WorktreeRecord:
        """Record a task-level outcome (completed / failed / abandoned / active)."""

        if status not in ("active", "completed", "failed", "abandoned"):
            raise GitError(
                "INVALID_ARGUMENTS", f"Cannot set status '{status}' directly"
            )

        with self.store.lock():
            record = self.get(worktree_id)

            if record.status in ("removing", "removed", "creating"):
                raise GitError(
                    "GIT_FAILURE",
                    f"Worktree is {record.status}; cannot change its status.",
                )

            return self._set(record, status, note)

    # ----------------------------------------------------------- assess

    def assess(self, record: WorktreeRecord) -> dict[str, Any]:
        """Observe the real state (disk + Git) of a worktree."""

        path = Path(record.path)
        exists = path.is_dir()
        entry = next(
            (e for e in self._git_worktrees() if _norm(e["path"]) == _norm(path)),
            None,
        )
        result: dict[str, Any] = {
            "worktree_id": record.worktree_id,
            "exists": exists,
            "registered_in_git": entry is not None,
            "locked": bool(entry and entry.get("locked")),
            "prunable": bool(entry and entry.get("prunable")),
            "branch_exists": self._branch_exists(record.branch),
            "dirty": False,
            "changes": {"staged": [], "unstaged": [], "untracked": [], "conflicted": []},
            "state": None,
            "commits_ahead": 0,
            "commits_elsewhere": False,
            "head": None,
        }

        if exists and entry is not None:
            status = get_status(path)
            result["changes"] = {
                key: status[key]
                for key in ("staged", "unstaged", "untracked", "conflicted")
            }
            result["dirty"] = not status["clean"]
            result["state"] = status["state"]
            result["head"] = status["head_sha"]

        if result["branch_exists"]:
            tip = self._git(
                ["rev-parse", f"refs/heads/{record.branch}"]
            ).stdout.strip()
            ahead = self._git(
                ["rev-list", "--count", f"{record.base_commit}..{tip}"]
            ).stdout.strip()
            result["commits_ahead"] = int(ahead or 0)
            result["branch_tip"] = tip
            containing = self._git(
                [
                    "for-each-ref",
                    f"--contains={tip}",
                    "--format=%(refname)",
                    "refs/heads",
                    "refs/remotes",
                    "refs/tags",
                ]
            ).stdout.split()
            result["commits_elsewhere"] = any(
                ref != f"refs/heads/{record.branch}" for ref in containing
            )

        at_risk_commits = (
            result["commits_ahead"] > 0 and not result["commits_elsewhere"]
        )
        result["unreviewed"] = result["dirty"] or at_risk_commits
        reasons = []

        if result["dirty"]:
            reasons.append("uncommitted changes")

        if at_risk_commits:
            reasons.append(
                f"{result['commits_ahead']} commit(s) not present on any other ref"
            )

        if result["locked"]:
            reasons.append("worktree is locked")

        result["cleanup_blockers"] = reasons
        result["safe_to_remove"] = not reasons
        return result

    def describe(self, record: WorktreeRecord, assess: bool = True) -> dict[str, Any]:
        data = record.model_dump()

        if assess and record.status != "removed":
            data["assessment"] = self.assess(record)

        return data

    # ----------------------------------------------------------- remove

    def _check_recorded_path(self, record: WorktreeRecord) -> Path:
        path = Path(record.path)
        expected = self.managed_dir(record.task_id)

        if _norm(path) != _norm(expected) or record.branch != branch_for(
            record.task_id
        ):
            raise GitError(
                "PERMISSION_DENIED",
                "Recorded worktree path does not match the managed layout; "
                "refusing to remove it.",
                {"recorded": record.path, "expected": str(expected)},
            )

        return expected

    def _backup(self, record: WorktreeRecord, assessment: dict) -> dict[str, Any]:
        backups: dict[str, str] = {}
        complete = True
        notes: list[str] = []

        if assessment["dirty"] and assessment["exists"]:
            diff = get_diff(
                Path(record.path),
                mode="base",
                base="HEAD",
                include_untracked=True,
                max_bytes=MAX_BACKUP_BYTES,
            )

            if diff["truncated"]:
                raise GitError(
                    "RESOURCE_LIMIT",
                    "Uncommitted changes are too large to back up; "
                    "refusing to discard them.",
                )

            skipped = diff["skipped_untracked"]
            binary = [f["path"] for f in diff["files"] if f.get("binary")]

            if skipped or binary:
                complete = False
                notes.append(
                    "not captured in the patch: "
                    f"{[s['path'] for s in skipped] + binary}"
                )

            self.store.backups_dir.mkdir(parents=True, exist_ok=True)
            target = self.store.backups_dir / f"{record.worktree_id}.patch"
            atomic_write_bytes(target, diff["diff"].encode("utf-8"))
            backups["patch"] = str(target)

        if assessment["commits_ahead"] > 0 and not assessment["commits_elsewhere"]:
            ref = f"refs/aseos/removed/{record.worktree_id}"
            self._git(
                ["update-ref", ref, assessment["branch_tip"]], read_only=False
            )
            backups["ref"] = ref

        return {"files": backups, "complete": complete, "notes": notes}

    def _refuse_if_in_use(self, record: WorktreeRecord) -> None:
        """A running task owns its worktree: never remove it from under it."""

        from execution.recovery.lease import live_holder
        from execution.recovery.locations import this_host
        from execution.recovery.liveness import is_same_process
        from execution.recovery.ops import OperationsLog

        state = self.common_dir / "aseos"
        holder = live_holder(state, record.path)

        if holder:
            raise GitError(
                "PERMISSION_DENIED",
                "A running task holds this worktree; stop it before removing the worktree.",
                {
                    "worktree_id": record.worktree_id,
                    "holder": {k: holder.get(k) for k in ("owner_id", "pid", "task_id")},
                },
            )

        for op in OperationsLog(state).list(record.path):
            alive = (
                is_same_process(op.pid, op.pid_created) if op.host == this_host() else True
            )

            if alive:
                raise GitError(
                    "PERMISSION_DENIED",
                    f"{op.tool} is still running in this worktree; wait for it or cancel it first.",
                    {"worktree_id": record.worktree_id, "action_id": op.action_id},
                )

    def _finish_interrupted_removal(
        self, record: WorktreeRecord, assessment: dict[str, Any]
    ) -> dict[str, Any]:
        """Git can unregister a worktree and delete its files yet fail on the
        directory itself (a process still has it as its working directory, an
        indexer holds a handle). What remains is an empty folder Git no longer
        knows. Removing an EMPTY folder cannot lose anything, so do that and
        look again; a folder with anything in it is still refused."""

        path = Path(record.path)

        try:
            if any(path.iterdir()):
                return assessment

            path.rmdir()  # fails on a non-empty folder: that is the safety
        except OSError as exc:
            raise GitError(
                "GIT_FAILURE",
                "The worktree folder is empty and no longer known to Git, but "
                f"it cannot be removed yet ({exc.strerror or exc}); something is "
                "probably still using it. Try again after that stops.",
                {"path": record.path, "retryable": True},
            ) from exc

        return self.assess(record)

    def remove(
        self, worktree_id: str, *, discard_confirmed: bool = False
    ) -> dict[str, Any]:
        """Remove a worktree directory (and its branch when that loses nothing)."""

        with self.store.lock():
            record = self.get(worktree_id)

            if record.status == "removed":
                return {"worktree_id": worktree_id, "removed": True, "already": True}

            self._check_recorded_path(record)
            self._refuse_if_in_use(record)
            assessment = self.assess(record)

            if assessment["exists"] and not assessment["registered_in_git"]:
                assessment = self._finish_interrupted_removal(record, assessment)

            if assessment["exists"] and not assessment["registered_in_git"]:
                raise GitError(
                    "PERMISSION_DENIED",
                    "The directory is not a Git worktree of this repository; "
                    "refusing to delete it.",
                    {"path": record.path},
                )

            if assessment["locked"]:
                raise GitError(
                    "PERMISSION_DENIED",
                    "Worktree is locked (git worktree lock); unlock it first.",
                    {"worktree_id": worktree_id},
                )

            if assessment["unreviewed"] and not discard_confirmed:
                raise GitError(
                    "PERMISSION_DENIED",
                    "Worktree has unreviewed work "
                    f"({', '.join(assessment['cleanup_blockers'])}); removal "
                    "needs explicit confirmation.",
                    {
                        "worktree_id": worktree_id,
                        "blockers": assessment["cleanup_blockers"],
                        "changes": assessment["changes"],
                        "commits_ahead": assessment["commits_ahead"],
                    },
                )

            backup = (
                self._backup(record, assessment)
                if assessment["unreviewed"]
                else {"files": {}, "complete": True, "notes": []}
            )
            previous = record.status
            record.backups = backup["files"]
            self._set(record, "removing")

            try:
                if assessment["exists"]:
                    args = ["worktree", "remove", record.path]

                    if assessment["dirty"]:
                        args.insert(2, "--force")

                    self._git(args, read_only=False, timeout=120)
                else:
                    self._git(["worktree", "prune"], read_only=False)
            except Exception:
                self._set(record, previous)
                raise

            branch_deleted = False
            branch_kept_reason = None

            if assessment["branch_exists"]:
                at_risk = (
                    assessment["commits_ahead"] > 0
                    and not assessment["commits_elsewhere"]
                )

                if not at_risk or "ref" in backup["files"]:
                    self._git(
                        ["branch", "-D", record.branch], read_only=False
                    )
                    branch_deleted = True
                else:
                    branch_kept_reason = "has commits that exist nowhere else"

            self._set(record, "removed", note="; ".join(backup["notes"]))
            return {
                "worktree_id": worktree_id,
                "removed": True,
                "discarded_unreviewed": assessment["unreviewed"],
                "branch_deleted": branch_deleted,
                "branch_kept_reason": branch_kept_reason,
                "backup": backup,
            }

    # -------------------------------------------------------- reconcile

    def reconcile(self) -> dict[str, Any]:
        """Compare records, Git's worktree list and the disk after a restart.

        Only record statuses change. Nothing on disk is deleted, and Git
        administrative entries are not pruned (see `prune_missing`).
        """

        with self.store.lock():
            records, corrupt = self.store.list()
            entries = self._git_worktrees()
            git_paths = {_norm(e["path"]): e for e in entries}
            report: dict[str, Any] = {
                "ok": [],
                "missing": [],
                "interrupted": [],
                "recovered": [],
                "detached_dirs": [],
                "orphan_dirs": [],
                "unrecorded_git_worktrees": [],
                "prune_candidates": [],
                "corrupt_records": corrupt,
            }
            known: set[str] = set()

            for record in records:
                if record.status == "removed":
                    continue

                key = _norm(record.path)
                known.add(key)
                on_disk = Path(record.path).is_dir()
                in_git = key in git_paths

                if record.status == "creating":
                    if on_disk and in_git:
                        self._set(record, "active", "recovered after interrupted create")
                        report["recovered"].append(record.worktree_id)
                    elif not on_disk and not in_git:
                        self.store.delete(record.worktree_id)
                        report["recovered"].append(record.worktree_id)
                    else:
                        report["interrupted"].append(record.worktree_id)

                    continue

                if record.status == "removing":
                    if not on_disk and not in_git:
                        self._set(record, "removed", "recovered after interrupted removal")
                        report["recovered"].append(record.worktree_id)
                    else:
                        report["interrupted"].append(record.worktree_id)

                    continue

                if not on_disk:
                    if record.status != "missing":
                        self._set(record, "missing", "directory not found")

                    report["missing"].append(record.worktree_id)

                    if in_git:
                        report["prune_candidates"].append(record.worktree_id)
                elif not in_git:
                    report["detached_dirs"].append(record.worktree_id)
                else:
                    if record.status == "missing":
                        self._set(record, "active", "directory reappeared")

                    report["ok"].append(record.worktree_id)

            if self.root.is_dir():
                for child in sorted(self.root.iterdir()):
                    if child.is_dir() and _norm(child) not in known:
                        report["orphan_dirs"].append(str(child))

            for key, entry in git_paths.items():
                if key == _norm(self.repo) or key in known:
                    continue

                try:
                    Path(entry["path"]).resolve().relative_to(self.root)
                except ValueError:
                    continue

                report["unrecorded_git_worktrees"].append(entry["path"])

            return report

    def prune_missing(self, worktree_id: str) -> dict[str, Any]:
        """Drop Git's stale entry for a worktree whose directory is gone.

        The branch is kept (it may hold the only copy of the work).
        """

        with self.store.lock():
            record = self.get(worktree_id)

            if record.status not in ("missing", "active", "completed", "failed", "abandoned"):
                raise GitError(
                    "GIT_FAILURE", f"Worktree is {record.status}; cannot prune."
                )

            if Path(record.path).exists():
                raise GitError(
                    "GIT_FAILURE",
                    "The directory still exists; use remove instead.",
                )

            self._git(["worktree", "prune"], read_only=False)
            assessment = self.assess(record)
            self._set(record, "removed", "pruned after directory was lost")
            return {
                "worktree_id": worktree_id,
                "branch_kept": assessment["branch_exists"],
                "branch": record.branch,
                "commits_ahead": assessment["commits_ahead"],
            }


def live_statuses() -> tuple[str, ...]:
    return LIVE_STATUSES
