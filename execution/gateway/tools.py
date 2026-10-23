from pydantic import BaseModel, ConfigDict, Field, field_validator

from execution.commands.runner import run_command
from execution.gateway.tool import (
    Tool,
    ToolContext,
    ToolError,
    ToolOutput,
    ToolSpec,
    resolve_in_workspace,
)
from execution.patches.apply import apply_patch
from execution.testing.runner import run_tests

SEARCH_IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules"}
SEARCH_MAX_MATCHES = 100

CAP_FS_READ = "fs.read"
CAP_FS_WRITE = "fs.write"
CAP_GIT_READ = "git.read"
CAP_TEST_RUN = "test.run"
CAP_SHELL_RUN = "shell.run"

# Capabilities granted by default. shell.run is intentionally absent.
DEFAULT_CAPABILITIES = frozenset(
    {CAP_FS_READ, CAP_FS_WRITE, CAP_GIT_READ, CAP_TEST_RUN}
)


class _Args(BaseModel):
    model_config = ConfigDict(extra="ignore")


def _non_empty(value: str) -> str:
    if not value.strip():
        raise ValueError("must be a non-empty string")

    return value


class ReadArgs(_Args):
    path: str

    _check_path = field_validator("path")(_non_empty)


class SearchArgs(_Args):
    query: str
    path: str = "."

    _check_query = field_validator("query")(_non_empty)
    _check_path = field_validator("path")(_non_empty)


class EditArgs(_Args):
    file: str
    old: str
    new: str

    _check_file = field_validator("file")(_non_empty)


class NoArgs(_Args):
    pass


class RunCommandArgs(_Args):
    command: str | list[str] | None = Field(default=None)


class ReadTool(Tool):
    spec = ToolSpec(
        name="repo.read",
        action_type="READ",
        description="Read a UTF-8 text file by workspace-relative path.",
        args_model=ReadArgs,
        capabilities=frozenset({CAP_FS_READ}),
        read_only=True,
        idempotent=True,
        timeout_seconds=30,
    )

    def run(self, context: ToolContext, args: ReadArgs) -> ToolOutput:
        file_path = resolve_in_workspace(context.workspace, args.path)

        if not file_path.is_file():
            raise ToolError("INVALID_ARGUMENTS", "Requested file does not exist")

        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError(
                "INVALID_ARGUMENTS", "File is not valid UTF-8 text"
            ) from exc

        return ToolOutput(stdout=content)


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


class EditTool(Tool):
    spec = ToolSpec(
        name="patch.apply",
        action_type="EDIT",
        description="Replace a unique text span in one workspace file.",
        args_model=EditArgs,
        capabilities=frozenset({CAP_FS_WRITE}),
        read_only=False,
        timeout_seconds=30,
    )

    def run(self, context: ToolContext, args: EditArgs) -> ToolOutput:
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
            data={"changed_files": [args.file]},
        )


class RunTestTool(Tool):
    spec = ToolSpec(
        name="test.run",
        action_type="RUN_TEST",
        description="Run the workspace test suite.",
        args_model=NoArgs,
        capabilities=frozenset({CAP_TEST_RUN}),
        read_only=False,
        timeout_seconds=120,
    )

    def run(self, context: ToolContext, args: NoArgs) -> ToolOutput:
        result = run_tests(str(context.workspace))
        return _command_output(result, failure_category="TEST_FAILURE")


class GitDiffTool(Tool):
    spec = ToolSpec(
        name="git.diff",
        action_type="GIT_DIFF",
        description="Return the unstaged Git diff of the workspace.",
        args_model=NoArgs,
        capabilities=frozenset({CAP_GIT_READ}),
        read_only=True,
        idempotent=True,
        timeout_seconds=30,
    )

    def run(self, context: ToolContext, args: NoArgs) -> ToolOutput:
        result = run_command(
            ["git", "diff", "--"], cwd=str(context.workspace), timeout=10
        )
        return _command_output(result, failure_category="GIT_FAILURE")


class RunCommandTool(Tool):
    """Registered but gated: needs the shell.run capability, which is not
    granted by default. No implementation exists until a command policy does."""

    spec = ToolSpec(
        name="shell.run",
        action_type="RUN_COMMAND",
        description="Run an explicitly permitted development command.",
        args_model=RunCommandArgs,
        capabilities=frozenset({CAP_SHELL_RUN}),
        read_only=False,
        timeout_seconds=60,
    )

    def run(self, context: ToolContext, args: RunCommandArgs) -> ToolOutput:
        raise ToolError(
            "PERMISSION_DENIED",
            "shell.run is disabled: no command policy is implemented.",
        )


def _command_output(result: dict, failure_category: str) -> ToolOutput:
    status = result.get("status")
    stdout = result.get("stdout") or ""
    stderr = result.get("stderr") or ""
    exit_code = result.get("exit_code")

    if status == "PASS":
        return ToolOutput(stdout=stdout, stderr=stderr, exit_code=exit_code)

    category = {
        "FAIL": failure_category,
        "TIMEOUT": "TIMEOUT",
    }.get(status, "PROCESS_FAILURE")

    raise ToolError(
        category,  # type: ignore[arg-type]
        stderr or f"Command ended with status {status}",
        stdout=stdout,
        stderr=stderr or None,
        exit_code=exit_code,
    )


def default_tools() -> list[Tool]:
    return [
        ReadTool(),
        SearchTool(),
        EditTool(),
        RunTestTool(),
        GitDiffTool(),
        RunCommandTool(),
    ]
