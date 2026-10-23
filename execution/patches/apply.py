from pathlib import Path


def apply_patch(worktree: str, patch: dict) -> dict:
    """Apply a text replacement safely within an isolated worktree."""
    try:
        root = Path(worktree).resolve()
        file_path = (root / patch["file"]).resolve()

        # Prevent patches from modifying files outside the worktree.
        if not file_path.is_relative_to(root):
            return {
                "status": "ERROR",
                "message": "Patch path escapes the worktree",
                "category": "PERMISSION_DENIED",
            }

        if not file_path.is_file():
            return {
                "status": "ERROR",
                "message": f"File not found: {patch['file']}",
                "category": "INVALID_ARGUMENTS",
            }

        content = file_path.read_text()
        old_text = patch["old"]
        new_text = patch["new"]

        if not old_text:
            return {
                "status": "ERROR",
                "message": "Target text cannot be empty",
                "category": "INVALID_ARGUMENTS",
            }

        occurrences = content.count(old_text)

        if occurrences == 0:
            return {
                "status": "ERROR",
                "message": "Target text not found",
                "category": "PATCH_CONFLICT",
            }

        if occurrences > 1:
            return {
                "status": "ERROR",
                "message": "Target text occurs multiple times",
                "category": "PATCH_CONFLICT",
            }

        file_path.write_text(content.replace(old_text, new_text))

        return {
            "status": "PASS",
            "file": patch["file"],
            "message": "Patch applied successfully",
        }

    except (KeyError, TypeError, OSError, ValueError) as exc:
        return {
            "status": "ERROR",
            "message": str(exc),
            "category": (
                "INTERNAL_ERROR" if isinstance(exc, OSError)
                else "INVALID_ARGUMENTS"
            ),
        }
