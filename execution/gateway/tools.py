from execution.gateway.artifact_tools import ArtifactListTool, ArtifactReadTool
from execution.gateway.capabilities import DEFAULT_CAPABILITIES
from execution.gateway.command_tools import (
    RunCommandTool,
    RunTestTool,
    TestFreshnessTool,
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
from execution.gateway.tool import Tool

__all__ = ["DEFAULT_CAPABILITIES", "default_tools"]


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
        TestFreshnessTool(),
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
        ArtifactReadTool(),
        ArtifactListTool(),
    ]
