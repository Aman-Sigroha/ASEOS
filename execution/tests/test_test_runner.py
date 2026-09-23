from execution.testing.runner import run_tests


def test_run_tests_passes(tmp_path):
    test_file = tmp_path / "test_example.py"

    test_file.write_text(
        "def test_example():\n"
        "    assert 1 + 1 == 2\n"
    )

    result = run_tests(str(tmp_path))

    assert result["status"] == "PASS"
    assert result["exit_code"] == 0


def test_run_tests_fails(tmp_path):
    test_file = tmp_path / "test_example.py"

    test_file.write_text(
        "def test_example():\n"
        "    assert 1 + 1 == 3\n"
    )

    result = run_tests(str(tmp_path))

    assert result["status"] == "FAIL"
    assert result["exit_code"] != 0
