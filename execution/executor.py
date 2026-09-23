from execution.git.worktree import create_worktree, remove_worktree
from execution.patches.apply import apply_patch
from execution.testing.runner import run_tests
from execution.git.operations import git_diff


def execute_task(
    repo_path: str,
    task_id: str,
    patch: dict,
) -> dict:
    """
    Execute an engineering task inside an isolated Git worktree.

    Steps:
    1. Create isolated worktree
    2. Apply controlled patch
    3. Run tests
    4. Collect Git diff
    5. Remove worktree

    Returns a structured execution result.
    """

    worktree = None

    try:
        # 1. Create isolated worktree
        worktree = create_worktree(
            repo_path,
            task_id,
        )

        # 2. Apply patch
        patch_result = apply_patch(
            worktree,
            patch,
        )

        if patch_result["status"] != "PASS":
            return {
                "status": "ERROR",
                "patch": patch_result,
                "tests": None,
                "diff": "",
            }

        # 3. Run tests
        test_result = run_tests(
            worktree,
        )

        # 4. Collect diff
        diff = git_diff(
            worktree,
        )

        # 5. Final result
        status = (
            "PASS"
            if test_result["status"] == "PASS"
            else test_result["status"]
        )

        return {
            "status": status,
            "patch": patch_result,
            "tests": test_result,
            "diff": diff,
        }

    finally:
        # Always clean up the worktree
        if worktree is not None:
            remove_worktree(
                repo_path,
                task_id,
            )
