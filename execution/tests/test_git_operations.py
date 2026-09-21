from execution.git.operations import git_status, git_diff


def test_git_status_returns_expected_structure():
    result = git_status(".")

    assert "branch" in result
    assert "clean" in result
    assert "modified_files" in result

    assert isinstance(result["branch"], str)
    assert isinstance(result["clean"], bool)
    assert isinstance(result["modified_files"], list)


def test_git_diff_returns_string():
    result = git_diff(".")

    assert isinstance(result, str)
