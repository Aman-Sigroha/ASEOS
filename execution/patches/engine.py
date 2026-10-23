"""Plan, validate and apply unified-diff patches inside a workspace.

Flow: parse -> plan (pure: reads files, writes nothing) -> apply.
`plan_patch` is what validation uses. `apply_plan` writes every file via an
atomic replace and rolls back already-written files if a later step fails.
Rollback is best-effort, not a transaction: if the rollback itself fails the
result says exactly which files are in an unknown state.
"""

import difflib
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from execution.gateway.tool import (
    ToolError,
    ensure_writable_path,
    resolve_in_workspace,
)
from execution.patches.fileio import atomic_write_bytes
from execution.patches.unified import (
    FilePatch,
    Hunk,
    PatchParseError,
    parse_unified_diff,
    reverse_patch,
)

MAX_PATCH_BYTES = 200_000
MAX_PATCH_FILES = 20
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_REPORTED_DIFF_BYTES = 1_000_000


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class FilePlan:
    path: str
    target: Path
    kind: str  # "create" | "modify" | "delete"
    before: bytes | None
    after: bytes | None
    additions: int = 0
    deletions: int = 0
    hunk_offsets: list[int] = field(default_factory=list)


@dataclass
class PatchPlan:
    patch_id: str
    workspace: Path
    files: list[FilePlan]
    reverse: bool = False

    @property
    def paths(self) -> list[str]:
        return [f.path for f in self.files]


def _split_lines(text: str) -> list[str]:
    """Split keeping line endings; a final unterminated line is kept as is."""

    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]

    if parts[-1]:
        lines.append(parts[-1])

    return lines


def _norm(line: str) -> str:
    return line.rstrip("\r\n")


def _dominant_eol(lines: list[str]) -> str:
    crlf = sum(1 for line in lines if line.endswith("\r\n"))
    lf = sum(1 for line in lines if line.endswith("\n")) - crlf
    return "\r\n" if crlf > lf else "\n"


def _block_matches(lines: list[str], index: int, block: list[str]) -> bool:
    if index < 0 or index + len(block) > len(lines):
        return False

    return all(_norm(lines[index + k]) == block[k] for k in range(len(block)))


def _locate_hunk(
    lines: list[str], hunk: Hunk, cursor: int, path: str, number: int
) -> int:
    block = [h.text for h in hunk.lines if h.op in (" ", "-")]
    expected = hunk.old_start - 1 if hunk.old_count > 0 else hunk.old_start
    expected = max(expected, 0)

    if not block:
        if cursor <= expected <= len(lines):
            return expected

        raise ToolError(
            "PATCH_CONFLICT",
            f"{path}: hunk {number} inserts at line {hunk.old_start}, which "
            "is outside the file or overlaps a previous hunk.",
            data={"path": path, "hunk": number},
        )

    if expected >= cursor and _block_matches(lines, expected, block):
        return expected

    candidates = [
        i
        for i in range(cursor, len(lines) - len(block) + 1)
        if _block_matches(lines, i, block)
    ]

    if len(candidates) == 1:
        return candidates[0]

    if not candidates:
        found = (
            [_norm(x) for x in lines[expected : expected + len(block)]]
            if expected < len(lines)
            else []
        )
        raise ToolError(
            "PATCH_CONFLICT",
            f"{path}: hunk {number} does not match the file near line "
            f"{expected + 1} (context or removed lines differ).",
            data={
                "path": path,
                "hunk": number,
                "expected_lines": block[:10],
                "found_lines": found[:10],
            },
        )

    raise ToolError(
        "PATCH_CONFLICT",
        f"{path}: hunk {number} matches {len(candidates)} places "
        f"(lines {[c + 1 for c in candidates[:5]]}); add more context.",
        data={"path": path, "hunk": number, "candidates": candidates[:5]},
    )


def _apply_hunks(
    text: str, hunks: list[Hunk], path: str
) -> tuple[str, list[int], int, int]:
    lines = _split_lines(text)
    eol = _dominant_eol(lines)
    out: list[str] = []
    cursor = 0
    offsets: list[int] = []
    additions = deletions = 0

    for number, hunk in enumerate(hunks, start=1):
        position = _locate_hunk(lines, hunk, cursor, path, number)
        expected = (
            hunk.old_start - 1 if hunk.old_count > 0 else hunk.old_start
        )
        offsets.append(position - max(expected, 0))
        out.extend(lines[cursor:position])
        index = position

        for hunk_line in hunk.lines:
            if hunk_line.op == " ":
                out.append(lines[index])
                index += 1
            elif hunk_line.op == "-":
                index += 1
                deletions += 1
            else:
                if out and not out[-1].endswith("\n"):
                    out[-1] += eol

                out.append(
                    hunk_line.text + ("" if hunk_line.no_newline else eol)
                )
                additions += 1

        cursor = index

    out.extend(lines[cursor:])
    return "".join(out), offsets, additions, deletions


def _decode(raw: bytes, path: str) -> str:
    if b"\x00" in raw[:8192]:
        raise ToolError(
            "INVALID_ARGUMENTS", f"{path}: file appears to be binary"
        )

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolError(
            "INVALID_ARGUMENTS", f"{path}: file is not valid UTF-8 text"
        ) from exc


def plan_patch(
    workspace: Path,
    patch_text: str,
    *,
    base: dict[str, str] | None = None,
    reverse: bool = False,
    allow_delete: bool = False,
) -> PatchPlan:
    """Parse and fully validate a patch without touching the filesystem."""

    if len(patch_text.encode("utf-8")) > MAX_PATCH_BYTES:
        raise ToolError(
            "RESOURCE_LIMIT", f"Patch exceeds {MAX_PATCH_BYTES} bytes."
        )

    try:
        file_patches: list[FilePatch] = parse_unified_diff(patch_text)
    except PatchParseError as exc:
        raise ToolError("INVALID_ARGUMENTS", f"Invalid patch: {exc}") from exc

    if reverse:
        file_patches = reverse_patch(file_patches)

    if len(file_patches) > MAX_PATCH_FILES:
        raise ToolError(
            "RESOURCE_LIMIT",
            f"Patch touches {len(file_patches)} files; the limit is "
            f"{MAX_PATCH_FILES}.",
        )

    paths = [fp.path for fp in file_patches]

    if len(set(paths)) != len(paths):
        raise ToolError(
            "INVALID_ARGUMENTS", "Patch lists the same file more than once"
        )

    base = {k: v.lower() for k, v in (base or {}).items()}
    unknown = set(base) - set(paths)

    if unknown:
        raise ToolError(
            "INVALID_ARGUMENTS",
            f"base lists files not in the patch: {sorted(unknown)}",
        )

    plans = [
        _plan_file(workspace, fp, base.get(fp.path), allow_delete)
        for fp in file_patches
    ]
    patch_id = sha256_hex(patch_text.encode("utf-8"))
    return PatchPlan(patch_id, workspace, plans, reverse)


def _plan_file(
    workspace: Path,
    fp: FilePatch,
    base_sha: str | None,
    allow_delete: bool,
) -> FilePlan:
    path = fp.path
    target = resolve_in_workspace(workspace, path)
    ensure_writable_path(workspace, target)

    if target == workspace:
        raise ToolError("INVALID_ARGUMENTS", f"{path}: not a file path")

    if Path(workspace, path).is_symlink() or target.is_symlink():
        raise ToolError(
            "PERMISSION_DENIED", f"{path}: refusing to patch a symlink"
        )

    if target.is_dir():
        raise ToolError("INVALID_ARGUMENTS", f"{path}: path is a directory")

    exists = target.exists()
    before = None

    if exists:
        if target.stat().st_size > MAX_FILE_BYTES:
            raise ToolError(
                "RESOURCE_LIMIT", f"{path}: file is larger than the limit"
            )

        before = target.read_bytes()

    if base_sha is not None:
        current = sha256_hex(before) if before is not None else None

        if current != base_sha:
            raise ToolError(
                "PATCH_CONFLICT",
                f"{path}: file does not match the expected base version.",
                data={
                    "path": path,
                    "expected_sha256": base_sha,
                    "actual_sha256": current,
                },
            )

    if fp.is_create:
        if exists:
            raise ToolError(
                "PATCH_CONFLICT",
                f"{path}: patch creates this file but it already exists.",
                data={"path": path, "actual_sha256": sha256_hex(before)},
            )

        new_text, offsets, additions, deletions = _apply_hunks("", fp.hunks, path)
        return FilePlan(
            path, target, "create", None, new_text.encode("utf-8"),
            additions, deletions, offsets,
        )

    if not exists:
        raise ToolError(
            "PATCH_CONFLICT", f"{path}: file does not exist.",
            data={"path": path},
        )

    new_text, offsets, additions, deletions = _apply_hunks(
        _decode(before, path), fp.hunks, path
    )

    if fp.is_delete:
        if not allow_delete:
            raise ToolError(
                "PERMISSION_DENIED",
                f"{path}: deleting files requires the 'fs.delete' capability.",
            )

        if new_text != "":
            raise ToolError(
                "PATCH_CONFLICT",
                f"{path}: delete patch does not cover the whole file.",
                data={"path": path},
            )

        return FilePlan(
            path, target, "delete", before, None, additions, deletions, offsets
        )

    return FilePlan(
        path, target, "modify", before, new_text.encode("utf-8"),
        additions, deletions, offsets,
    )


# ---------------------------------------------------------------- apply


def _current_bytes(target: Path) -> bytes | None:
    return target.read_bytes() if target.exists() else None


def apply_plan(plan: PatchPlan, action_id: str = "") -> dict:
    """Write a validated plan; return the result built from disk state."""

    for file_plan in plan.files:
        if _current_bytes(file_plan.target) != file_plan.before:
            raise ToolError(
                "PATCH_CONFLICT",
                f"{file_plan.path}: file changed after the patch was "
                "validated; nothing was written.",
                data={"path": file_plan.path, "written": []},
            )

    done: list[FilePlan] = []
    created_dirs: list[Path] = []

    try:
        for file_plan in plan.files:
            _write_one(plan.workspace, file_plan, created_dirs)
            done.append(file_plan)

        _verify(plan)
    except Exception as exc:
        failed = _rollback(done, created_dirs)
        rolled_back = not failed
        message = (
            f"Patch application failed ({type(exc).__name__}: {exc}); "
            + (
                "all changes were rolled back."
                if rolled_back
                else "rollback was incomplete."
            )
        )
        category = exc.category if isinstance(exc, ToolError) else "INTERNAL_ERROR"
        raise ToolError(
            category,  # type: ignore[arg-type]
            message,
            data={
                "rolled_back": rolled_back,
                "partial": not rolled_back,
                "unrestored_files": failed,
            },
        ) from exc

    return _report(plan, action_id)


def _write_one(
    workspace: Path, file_plan: FilePlan, created_dirs: list[Path]
) -> None:
    target = file_plan.target

    if file_plan.kind == "delete":
        target.unlink()
        return

    missing = []
    parent = target.parent

    while parent != workspace and not parent.exists():
        missing.append(parent)
        parent = parent.parent

    for directory in reversed(missing):
        directory.mkdir()
        created_dirs.append(directory)

    expected_before = file_plan.before

    def unchanged() -> None:
        if _current_bytes(target) != expected_before:
            raise ToolError(
                "PATCH_CONFLICT",
                f"{file_plan.path}: file changed while the patch was "
                "being applied.",
            )

    atomic_write_bytes(target, file_plan.after or b"", unchanged)


def _verify(plan: PatchPlan) -> None:
    for file_plan in plan.files:
        actual = _current_bytes(file_plan.target)

        if actual != file_plan.after:
            raise ToolError(
                "INTERNAL_ERROR",
                f"{file_plan.path}: post-write verification failed.",
            )


def _rollback(done: list[FilePlan], created_dirs: list[Path]) -> list[str]:
    failed: list[str] = []

    for file_plan in reversed(done):
        try:
            if file_plan.before is None:
                file_plan.target.unlink(missing_ok=True)
            else:
                atomic_write_bytes(file_plan.target, file_plan.before)
        except OSError:
            failed.append(file_plan.path)

    for directory in reversed(created_dirs):
        try:
            directory.rmdir()
        except OSError:
            pass

    return failed


def _unified(path: str, kind: str, before: bytes | None, after: bytes | None) -> str:
    old = (before or b"").decode("utf-8").splitlines(keepends=True)
    new = (after or b"").decode("utf-8").splitlines(keepends=True)
    fromfile = "/dev/null" if kind == "create" else f"a/{path}"
    tofile = "/dev/null" if kind == "delete" else f"b/{path}"
    out: list[str] = []

    for line in difflib.unified_diff(old, new, fromfile, tofile):
        if line.endswith("\n"):
            out.append(line)
        else:
            out.append(line + "\n\\ No newline at end of file\n")

    return "".join(out)


def _report(plan: PatchPlan, action_id: str) -> dict:
    """Describe what is actually on disk now (not what the patch intended)."""

    files = []
    diff_parts = []

    for file_plan in plan.files:
        actual = _current_bytes(file_plan.target)
        files.append(
            {
                "path": file_plan.path,
                "action": {
                    "create": "created",
                    "modify": "modified",
                    "delete": "deleted",
                }[file_plan.kind],
                "sha256_before": (
                    None if file_plan.before is None
                    else sha256_hex(file_plan.before)
                ),
                "sha256_after": None if actual is None else sha256_hex(actual),
                "additions": file_plan.additions,
                "deletions": file_plan.deletions,
                "hunk_offsets": file_plan.hunk_offsets,
            }
        )
        diff_parts.append(
            _unified(file_plan.path, file_plan.kind, file_plan.before, actual)
        )

    diff = "".join(diff_parts)
    truncated = len(diff.encode("utf-8")) > MAX_REPORTED_DIFF_BYTES

    if truncated:
        diff = diff.encode("utf-8")[:MAX_REPORTED_DIFF_BYTES].decode(
            "utf-8", errors="ignore"
        )

    return {
        "patch_id": plan.patch_id,
        "action_id": action_id,
        "reverse": plan.reverse,
        "files": files,
        "changed_files": plan.paths,
        "additions": sum(f["additions"] for f in files),
        "deletions": sum(f["deletions"] for f in files),
        "applied_diff": diff,
        "applied_diff_truncated": truncated,
    }


def describe_plan(plan: PatchPlan) -> dict:
    """Validation summary (no writes happened)."""

    return {
        "patch_id": plan.patch_id,
        "reverse": plan.reverse,
        "valid": True,
        "files": [
            {
                "path": f.path,
                "action": f.kind,
                "sha256_before": (
                    None if f.before is None else sha256_hex(f.before)
                ),
                "additions": f.additions,
                "deletions": f.deletions,
                "hunk_offsets": f.hunk_offsets,
            }
            for f in plan.files
        ],
        "changed_files": plan.paths,
    }
