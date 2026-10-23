import subprocess

import pytest

from execution.git.operations import git_diff, git_status


@pytest.fixture
def repo(tmp_path):
    def git(*args):
        subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, capture_output=True
        )

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "ASEOS Test")
    (tmp_path / "example.txt").write_text("hello\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "initial commit")
    return tmp_path


def test_git_status_returns_expected_structure(repo):
    result = git_status(str(repo))

    assert isinstance(result["branch"], str)
    assert result["clean"] is True
    assert result["modified_files"] == []


def test_git_status_reports_modified_files(repo):
    (repo / "example.txt").write_text("changed\n", encoding="utf-8")

    result = git_status(str(repo))

    assert result["clean"] is False
    assert result["modified_files"] == ["example.txt"]


def test_git_diff_returns_string(repo):
    (repo / "example.txt").write_text("changed\n", encoding="utf-8")

    result = git_diff(str(repo))

    assert isinstance(result, str)
    assert "+changed" in result


def test_git_diff_handles_non_cp1252_characters(repo):
    (repo / "example.txt").write_text("┌─ box → ✓\n", encoding="utf-8")

    assert "┌─ box → ✓" in git_diff(str(repo))
