"""Filesystem tools.

Symlink policy (enforced here and tested):
- Every path is resolved before use; a path whose resolved location is
  outside the workspace is PERMISSION_DENIED, even if the link itself sits
  inside the workspace.
- Reads may follow a symlink whose target is inside the workspace.
- Writes refuse to write *through* a symlink (the final path component must
  not be a link), so a link can never redirect a write.
- Writes to anything under a `.git` directory are denied.
"""

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from execution.gateway.capabilities import CAP_FS_READ, CAP_FS_WRITE
from execution.gateway.tool import (
    Tool,
    ToolContext,
    ToolError,
    ToolOutput,
    ToolSpec,
    ensure_writable_path,
    resolve_in_workspace,
)
from execution.patches.apply import apply_patch
from execution.patches.fileio import atomic_write_bytes

SEARCH_IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules"}
SEARCH_MAX_MATCHES = 100

MAX_READ_BYTES = 1_000_000  # default/maximum returned content size
HARD_READ_BYTES = 10 * 1024 * 1024  # larger files are refused outright
MAX_WRITE_BYTES = 1_000_000
BINARY_SNIFF_BYTES = 8192
LIST_DEFAULT_ENTRIES = 200
LIST_MAX_ENTRIES = 1000


class _Args(BaseModel):
    model_config = ConfigDict(extra="ignore")


def _non_empty(value: str) -> str:
    if not value.strip():
        raise ValueError("must be a non-empty string")

    return value


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:BINARY_SNIFF_BYTES]


def _load_text_file(file_path: Path) -> tuple[bytes, str]:
    """Read a bounded UTF-8 text file, returning raw bytes and decoded text."""

    if not file_path.is_file():
        raise ToolError("INVALID_ARGUMENTS", "Requested file does not exist")

    size = file_path.stat().st_size

    if size > HARD_READ_BYTES:
        raise ToolError(
            "RESOURCE_LIMIT",
            f"File is {size} bytes; the limit is {HARD_READ_BYTES} bytes.",
        )

    raw = file_path.read_bytes()

    if _looks_binary(raw):
        raise ToolError("INVALID_ARGUMENTS", "File appears to be binary")

    try:
        return raw, raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolError(
            "INVALID_ARGUMENTS", "File is not valid UTF-8 text"
        ) from exc


# ----------------------------------------------------------------- READ


class ReadArgs(_Args):
    path: str
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    max_bytes: int = Field(default=MAX_READ_BYTES, ge=1, le=MAX_READ_BYTES)

    _check_path = field_validator("path")(_non_empty)

    @model_validator(mode="after")
    def _check_range(self) -> "ReadArgs":
        if (
            self.start_line is not None
            and self.end_line is not None
            and self.end_line < self.start_line
        ):
            raise ValueError("end_line must be >= start_line")

        return self


class ReadTool(Tool):
    spec = ToolSpec(
        name="repo.read",
        action_type="READ",
        description=(
            "Read a UTF-8 text file (optionally a 1-based inclusive line "
            "range). Output is bounded; data.sha256 identifies the version."
        ),
        args_model=ReadArgs,
        capabilities=frozenset({CAP_FS_READ}),
        read_only=True,
        idempotent=True,
        timeout_seconds=30,
    )

    def run(self, context: ToolContext, args: ReadArgs) -> ToolOutput:
        file_path = resolve_in_workspace(context.workspace, args.path)
        raw, text = _load_text_file(file_path)

        lines = text.splitlines(keepends=True)
        total_lines = len(lines)
        data: dict = {
            "path": args.path,
            "sha256": sha256_hex(raw),
            "total_bytes": len(raw),
            "total_lines": total_lines,
        }

        selected = text

        if args.start_line is not None or args.end_line is not None:
            start = args.start_line or 1

            if start > total_lines:
                raise ToolError(
                    "INVALID_ARGUMENTS",
                    f"start_line {start} is beyond the end of the file "
                    f"({total_lines} lines).",
                )

            end = min(args.end_line or total_lines, total_lines)
            selected = "".join(lines[start - 1 : end])
            data["start_line"] = start
            data["end_line"] = end

        truncated = False
        encoded = selected.encode("utf-8")

        if len(encoded) > args.max_bytes:
            selected = encoded[: args.max_bytes].decode(
                "utf-8", errors="ignore"
            )
            truncated = True

        data["truncated"] = truncated
        return ToolOutput(stdout=selected, truncated=truncated, data=data)


# ------------------------------------------------------------- LIST_DIR


class ListDirArgs(_Args):
    path: str = "."
    max_entries: int = Field(
        default=LIST_DEFAULT_ENTRIES, ge=1, le=LIST_MAX_ENTRIES
    )

    _check_path = field_validator("path")(_non_empty)


class ListDirTool(Tool):
    spec = ToolSpec(
        name="repo.list_dir",
        action_type="LIST_DIR",
        description="List the entries of one workspace directory (not recursive).",
        args_model=ListDirArgs,
        capabilities=frozenset({CAP_FS_READ}),
        read_only=True,
        idempotent=True,
        timeout_seconds=30,
    )

    def run(self, context: ToolContext, args: ListDirArgs) -> ToolOutput:
        directory = resolve_in_workspace(context.workspace, args.path)

        if not directory.is_dir():
            raise ToolError("INVALID_ARGUMENTS", "Path is not a directory")

        entries = []

        for child in directory.iterdir():
            try:
                resolved = child.resolve()
                is_link = child.is_symlink()

                # A link pointing outside the workspace is hidden, not listed.
                if not resolved.is_relative_to(context.workspace):
                    continue

                is_dir = child.is_dir()
                size = None if is_dir else child.stat().st_size
            except OSError:
                continue

            entries.append(
                {
                    "name": child.name,
                    "type": "dir" if is_dir else "file",
                    "size": size,
                    "symlink": is_link,
                }
            )

        entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
        truncated = len(entries) > args.max_entries
        entries = entries[: args.max_entries]

        stdout = "\n".join(
            f"{e['type']:<4} "
            f"{'' if e['size'] is None else e['size']:>10} "
            f"{e['name']}{'/' if e['type'] == 'dir' else ''}"
            for e in entries
        )

        return ToolOutput(
            stdout=stdout or "(empty directory)",
            truncated=truncated,
            data={"entries": entries, "entry_count": len(entries)},
        )


# ------------------------------------------------------------ FILE_INFO


class FileInfoArgs(_Args):
    path: str

    _check_path = field_validator("path")(_non_empty)


class FileInfoTool(Tool):
    spec = ToolSpec(
        name="repo.file_info",
        action_type="FILE_INFO",
        description=(
            "Report whether a path exists plus type, size, mtime, binary "
            "flag and sha256. A missing path is a success with exists=false."
        ),
        args_model=FileInfoArgs,
        capabilities=frozenset({CAP_FS_READ}),
        read_only=True,
        idempotent=True,
        timeout_seconds=30,
    )

    def run(self, context: ToolContext, args: FileInfoArgs) -> ToolOutput:
        target = resolve_in_workspace(context.workspace, args.path)
        info: dict = {"path": args.path, "exists": target.exists()}

        if not info["exists"]:
            return ToolOutput(stdout="exists: false", data=info)

        stat = target.stat()
        info["type"] = "dir" if target.is_dir() else "file"
        info["size"] = stat.st_size
        info["modified"] = datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc
        ).isoformat()
        info["symlink"] = Path(context.workspace, args.path).is_symlink()

        if info["type"] == "file" and stat.st_size <= HARD_READ_BYTES:
            raw = target.read_bytes()
            info["sha256"] = sha256_hex(raw)
            info["binary"] = _looks_binary(raw)

        stdout = "\n".join(f"{key}: {value}" for key, value in info.items())
        return ToolOutput(stdout=stdout, data=info)


# --------------------------------------------------------------- SEARCH


class SearchArgs(_Args):
    query: str
    path: str = "."

    _check_query = field_validator("query")(_non_empty)
    _check_path = field_validator("path")(_non_empty)


class SearchTool(Tool):
    spec = ToolSpec(
        name="repo.search",
        action_type="SEARCH",
        description="Case-insensitive text search across workspace files.",
        args_model=SearchArgs,
        capabilities=frozenset({CAP_FS_READ}),
        read_only=True,
        idempotent=True,
        timeout_seconds=60,
    )

    def run(self, context: ToolContext, args: SearchArgs) -> ToolOutput:
        workspace = context.workspace
        search_root = resolve_in_workspace(workspace, args.path)

        if not search_root.exists():
            raise ToolError("INVALID_ARGUMENTS", "Search path does not exist")

        if search_root.is_file():
            files = [search_root]
        elif search_root.is_dir():
            files = []

            for path in search_root.rglob("*"):
                if not path.is_file():
                    continue

                if any(part in SEARCH_IGNORED_DIRS for part in path.parts):
                    continue

                resolved = path.resolve()

                if resolved.is_relative_to(workspace):
                    files.append(resolved)
        else:
            raise ToolError(
                "INVALID_ARGUMENTS", "Search path is not a file or directory"
            )

        needle = args.query.casefold()
        matches: list[str] = []

        for file_path in files:
            try:
                lines = file_path.read_text(encoding="utf-8").splitlines()
            except (UnicodeError, OSError):
                continue

            for line_number, line in enumerate(lines, start=1):
                if needle in line.casefold():
                    relative = file_path.relative_to(workspace)
                    matches.append(f"{relative}:{line_number}: {line}")

                    if len(matches) >= SEARCH_MAX_MATCHES:
                        break

            if len(matches) >= SEARCH_MAX_MATCHES:
                break

        return ToolOutput(
            stdout="\n".join(matches) if matches else "No matches found.",
            truncated=len(matches) >= SEARCH_MAX_MATCHES,
            data={"match_count": len(matches)},
        )


# ----------------------------------------------------------------- EDIT


class EditArgs(_Args):
    file: str
    old: str
    new: str

    _check_file = field_validator("file")(_non_empty)


class EditTool(Tool):
    spec = ToolSpec(
        name="patch.replace",
        action_type="EDIT",
        description="Replace a unique text span in one workspace file.",
        args_model=EditArgs,
        capabilities=frozenset({CAP_FS_WRITE}),
        read_only=False,
        timeout_seconds=30,
    )

    def run(self, context: ToolContext, args: EditArgs) -> ToolOutput:
        target = resolve_in_workspace(context.workspace, args.file)
        ensure_writable_path(context.workspace, target)

        result = apply_patch(
            str(context.workspace),
            {"file": args.file, "old": args.old, "new": args.new},
        )

        if result.get("status") != "PASS":
            raise ToolError(
                result.get("category", "INTERNAL_ERROR"),
                result.get("message", "Patch operation failed"),
                exit_code=1,
            )

        return ToolOutput(
            stdout=result.get("message", ""),
            data={
                "changed_files": [args.file],
                "sha256": sha256_hex(target.read_bytes()),
            },
        )


# ---------------------------------------------------------------- WRITE


class WriteArgs(_Args):
    path: str
    content: str
    expected_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-fA-F]{64}$"
    )

    _check_path = field_validator("path")(_non_empty)


class WriteTool(Tool):
    """Create a file, or replace one only if its version matches.

    - New file: expected_sha256 must be omitted.
    - Existing file: expected_sha256 (from repo.read / repo.file_info) is
      required and must match the current content, otherwise PATCH_CONFLICT.
    The write is atomic (temp file + os.replace) and verified afterwards.
    """

    spec = ToolSpec(
        name="repo.write",
        action_type="WRITE",
        description=(
            "Create a new file, or overwrite an existing one when "
            "expected_sha256 matches its current content."
        ),
        args_model=WriteArgs,
        capabilities=frozenset({CAP_FS_WRITE}),
        read_only=False,
        timeout_seconds=30,
    )

    def run(self, context: ToolContext, args: WriteArgs) -> ToolOutput:
        workspace = context.workspace
        payload = args.content.encode("utf-8")

        if len(payload) > MAX_WRITE_BYTES:
            raise ToolError(
                "RESOURCE_LIMIT",
                f"Content is {len(payload)} bytes; the limit is "
                f"{MAX_WRITE_BYTES} bytes.",
            )

        target = resolve_in_workspace(workspace, args.path)
        ensure_writable_path(workspace, target)

        if target == workspace:
            raise ToolError("INVALID_ARGUMENTS", "Path must name a file")

        if Path(workspace, args.path).is_symlink() or target.is_symlink():
            raise ToolError(
                "PERMISSION_DENIED", "Refusing to write through a symlink"
            )

        if target.is_dir():
            raise ToolError("INVALID_ARGUMENTS", "Path is a directory")

        previous_sha = self._check_expected_version(target, args)

        target.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(target, payload, previous_sha)

        written = target.read_bytes()
        actual_sha = sha256_hex(written)

        if actual_sha != sha256_hex(payload):
            raise ToolError(
                "INTERNAL_ERROR",
                "Post-write verification failed: file content differs from "
                "the requested content.",
            )

        return ToolOutput(
            stdout=f"Wrote {len(payload)} bytes to {args.path}",
            data={
                "changed_files": [args.path],
                "sha256": actual_sha,
                "previous_sha256": previous_sha,
                "created": previous_sha is None,
                "bytes": len(payload),
            },
        )

    @staticmethod
    def _current_sha(target: Path) -> str | None:
        if not target.exists():
            return None

        return sha256_hex(target.read_bytes())

    def _check_expected_version(
        self, target: Path, args: WriteArgs
    ) -> str | None:
        current = self._current_sha(target)
        expected = (
            args.expected_sha256.lower() if args.expected_sha256 else None
        )

        if current is None:
            if expected is not None:
                raise ToolError(
                    "PATCH_CONFLICT",
                    "File does not exist but expected_sha256 was supplied.",
                    data={"expected_sha256": expected, "actual_sha256": None},
                )

            return None

        if expected is None:
            raise ToolError(
                "INVALID_ARGUMENTS",
                "File already exists; supply expected_sha256 (from "
                "repo.read or repo.file_info) to overwrite it.",
                data={"actual_sha256": current},
            )

        if expected != current:
            raise ToolError(
                "PATCH_CONFLICT",
                "File changed since it was read; re-read it before writing.",
                data={"expected_sha256": expected, "actual_sha256": current},
            )

        return current

    def _atomic_write(
        self, target: Path, payload: bytes, previous_sha: str | None
    ) -> None:
        def unchanged() -> None:
            # Re-check right before replacing to narrow the race window.
            if self._current_sha(target) != previous_sha:
                raise ToolError(
                    "PATCH_CONFLICT",
                    "File changed while the write was being prepared.",
                )

        atomic_write_bytes(target, payload, unchanged)
