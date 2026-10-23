import subprocess


def git_status(repo_path: str) -> dict:
    """
    Return the current Git branch, clean status,
    and modified files.
    """

    branch_result = subprocess.run(
        ["git", "-C", repo_path, "branch", "--show-current"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )

    status_result = subprocess.run(
        ["git", "-C", repo_path, "status", "--porcelain"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )

    modified_files = []

    for line in status_result.stdout.splitlines():
        if line:
            modified_files.append(line[3:])

    return {
        "branch": branch_result.stdout.strip(),
        "clean": len(modified_files) == 0,
        "modified_files": modified_files,
    }


def git_diff(repo_path: str) -> str:
    """
    Return the current Git diff.
    """

    result = subprocess.run(
        ["git", "-C", repo_path, "diff"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )

    return result.stdout
