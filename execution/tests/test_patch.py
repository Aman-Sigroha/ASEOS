from execution.patches.apply import apply_patch


def test_apply_patch(tmp_path):
    file_path = tmp_path / "calculator.py"

    file_path.write_text(
        "def divide(a, b):\n"
        "    return a * b\n"
    )

    patch = {
        "file": "calculator.py",
        "old": "return a * b",
        "new": "return a / b",
    }

    result = apply_patch(str(tmp_path), patch)

    assert result["status"] == "PASS"

    assert file_path.read_text() == (
        "def divide(a, b):\n"
        "    return a / b\n"
    )


def test_patch_file_not_found(tmp_path):
    patch = {
        "file": "missing.py",
        "old": "old",
        "new": "new",
    }

    result = apply_patch(str(tmp_path), patch)

    assert result["status"] == "ERROR"


def test_patch_target_not_found(tmp_path):
    file_path = tmp_path / "calculator.py"
    file_path.write_text("return a + b")

    patch = {
        "file": "calculator.py",
        "old": "return a * b",
        "new": "return a / b",
    }

    result = apply_patch(str(tmp_path), patch)

    assert result["status"] == "ERROR"
