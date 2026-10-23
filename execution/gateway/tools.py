from pydantic import BaseModel, ConfigDict, Field

from execution.gateway.capabilities import (
    CAP_SHELL_RUN,
    CAP_TEST_RUN,
    DEFAULT_CAPABILITIES,
)
from execution.gateway.fs_tools import (
    EditTool,
    FileInfoTool,
    ListDirTool,
    ReadTool,
    SearchTool,
    WriteTool,
)
from execution.gateway.git_tools import (
    GitBranchTool,
    GitCommitTool,
    GitDiffTool,
    GitDiscardTool,
    GitInfoTool,
    GitStageTool,
    GitStatusTool,
)
from execution.gateway.worktree_tools import (
    WorktreeCreateTool,
    WorktreeListTool,
    WorktreeReconcileTool,
    WorktreeRemoveTool,
)
from execution.gateway.patch_tools import PatchApplyTool, PatchValidateTool
from execution.gateway.tool import (
    Tool,
    ToolContext,
    ToolError,
    ToolOutput,
    ToolSpec,
)
from execution.testing.runner import run_tests

__all__ = ["DEFAULT_CAPABILITIES", "default_tools"]


class _Args(BaseModel):
    model_config = ConfigDict(extra="ignore")


class NoArgs(_Args):
    pass


class RunCommandArgs(_Args):
    command: str | list[str] | None = Field(default=None)


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
        ListDirTool(),
        FileInfoTool(),
        SearchTool(),
        EditTool(),
        WriteTool(),
        PatchValidateTool(),
        PatchApplyTool(),
        RunTestTool(),
        GitStatusTool(),
        GitDiffTool(),
        GitInfoTool(),
        GitStageTool(),
        GitCommitTool(),
        GitBranchTool(),
        GitDiscardTool(),
        WorktreeCreateTool(),
        WorktreeListTool(),
        WorktreeRemoveTool(),
        WorktreeReconcileTool(),
        RunCommandTool(),
    ]
