import shutil
import tempfile
from pathlib import Path

from execution.git.diff import get_diff
from execution.patches.apply import apply_patch
from execution.testing.runner import run_tests
from execution.worktrees.manager import WorktreeManager


FIXTURE_PATH = "fixtures/repositories/calculator"
TASK_ID = "fix-divide"


def main():
    fixture = Path(FIXTURE_PATH).resolve()

    # Work on a throwaway copy so the demo never touches the fixture repo.
    sandbox = Path(tempfile.mkdtemp(prefix="aseos-demo-"))
    repo_path = sandbox / "calculator"
    shutil.copytree(fixture, repo_path)

    print("=" * 60)
    print("ASEOS Candidate 4 - Execution Demo")
    print("=" * 60)

    manager = WorktreeManager(repo_path)

    print("\n[1] Creating isolated worktree...")

    record = manager.create(TASK_ID)
    worktree = record.path

    print(f"Worktree: {worktree}")
    print(f"Branch:   {record.branch} (base {record.base_commit[:10]})")

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

        print("\n[4] Git diff against the base commit...")

        diff = get_diff(
            Path(worktree), mode="base", base=record.base_commit
        )["diff"]

        print("--- DIFF ---")
        print(diff)

        print("\n[5] Final result")

        if test_result["status"] == "PASS":
            print("SUCCESS: Patch applied and tests passed.")
        else:
            print("FAILURE: Tests did not pass.")

        print("\n[6] Safe cleanup check...")

        assessment = manager.assess(record)
        print(
            "Unreviewed work:",
            ", ".join(assessment["cleanup_blockers"]) or "none",
        )

    finally:
        print("\n[7] Removing the demo worktree (explicitly confirmed)...")

        # The demo owns this throwaway copy, so discarding is intentional.
        manager.remove(record.worktree_id, discard_confirmed=True)
        shutil.rmtree(sandbox, ignore_errors=True)

        print("Worktree removed.")


if __name__ == "__main__":
    main()
