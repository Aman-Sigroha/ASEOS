from pathlib import Path


def apply_patch(worktree: str, patch: dict) -> dict:
    """
    Apply a structured text replacement to a file inside a worktree.

    Patch format:

    {
        "file": "calculator.py",
        "old": "return a * b",
        "new": "return a / b"
    }
    """

    file_path = Path(worktree) / patch["file"]

    if not file_path.exists():
        return {
            "status": "ERROR",
            "message": f"File not found: {patch['file']}",
        }

    content = file_path.read_text()

    old_text = patch["old"]
    new_text = patch["new"]

    if old_text not in content:
        return {
            "status": "ERROR",
            "message": "Target text not found",
        }

    occurrences = content.count(old_text)

    if occurrences > 1:
        return {
            "status": "ERROR",
            "message": "Target text occurs multiple times",
        }

    updated_content = content.replace(old_text, new_text)

    file_path.write_text(updated_content)

    return {
        "status": "PASS",
        "file": patch["file"],
        "message": "Patch applied successfully",
    }
