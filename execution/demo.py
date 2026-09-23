from pathlib import Path

from execution.git.worktree import create_worktree, remove_worktree
from execution.patches.apply import apply_patch
from execution.testing.runner import run_tests
from execution.git.operations import git_diff


REPO_PATH = "fixtures/repositories/calculator"
TASK_ID = "fix-divide"


def main():
    repo_path = str(Path(REPO_PATH).resolve())

    print("=" * 60)
    print("ASEOS Candidate 4 - Execution Demo")
    print("=" * 60)

    print("\n[1] Creating isolated worktree...")

    worktree = create_worktree(
        repo_path,
        TASK_ID,
    )

    print(f"Worktree: {worktree}")

    try:
        print("\n[2] Applying patch...")

        patch = {
            "file": "calculator.py",
            "old": "return a * b",
            "new": "return a / b",
        }

        patch_result = apply_patch(
            worktree,
            patch,
        )

        print(patch_result)

        if patch_result["status"] != "PASS":
            print("\nPatch failed.")
            return

        print("\n[3] Running tests...")

        test_result = run_tests(worktree)

        print(f"Status: {test_result['status']}")
        print(f"Exit code: {test_result['exit_code']}")

        if test_result["stdout"]:
            print("\n--- TEST OUTPUT ---")
            print(test_result["stdout"])

        if test_result["stderr"]:
            print("\n--- TEST ERRORS ---")
            print(test_result["stderr"])

        print("\n[4] Git diff...")

        diff = git_diff(worktree)

        print("--- DIFF ---")
        print(diff)

        print("\n[5] Final result")

        if test_result["status"] == "PASS":
            print("SUCCESS: Patch applied and tests passed.")
        else:
            print("FAILURE: Tests did not pass.")

    finally:
        print("\n[6] Removing worktree...")

        remove_worktree(
            repo_path,
            TASK_ID,
        )

        print("Worktree removed.")


if __name__ == "__main__":
    main()
