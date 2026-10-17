
import pytest

from runtime.agent.real_executor import RealActionExecutor
from runtime.schemas.action import Action


@pytest.mark.asyncio
async def test_read_file_inside_workspace(tmp_path):
    (tmp_path / "example.py").write_text(
        "answer = 42\n", encoding="utf-8"
    )
    executor = RealActionExecutor(str(tmp_path))

    result = await executor.execute(
        Action(
            id="read-1",
            type="READ",
            payload={"path": "example.py"},
        )
    )

    assert result.success is True
    assert "answer = 42" in result.stdout


@pytest.mark.asyncio
async def test_edit_replaces_unique_text(tmp_path):
    target = tmp_path / "calculator.py"
    target.write_text("return a * b\n", encoding="utf-8")
    executor = RealActionExecutor(str(tmp_path))

    result = await executor.execute(
        Action(
            id="edit-1",
            type="EDIT",
            payload={
                "file": "calculator.py",
                "old": "return a * b",
                "new": "return a / b",
            },
        )
    )

    assert result.success is True
    assert target.read_text(encoding="utf-8") == "return a / b\n"


@pytest.mark.asyncio
async def test_read_rejects_path_traversal(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    executor = RealActionExecutor(str(tmp_path))

    result = await executor.execute(
        Action(
            id="read-2",
            type="READ",
            payload={"path": "../outside.txt"},
        )
    )

    assert result.success is False
    assert "escapes" in result.stderr.lower()


@pytest.mark.asyncio
async def test_run_command_is_rejected(tmp_path):
    executor = RealActionExecutor(str(tmp_path))

    result = await executor.execute(
        Action(
            id="command-1",
            type="RUN_COMMAND",
            payload={"command": "some arbitrary command"},
        )
    )

    assert result.success is False
    assert "disabled" in result.stderr.lower()


@pytest.mark.asyncio
async def test_search_finds_matching_line(tmp_path):
    (tmp_path / "example.py").write_text(
        "def calculate():\n    return 42\n",
        encoding="utf-8",
    )
    executor = RealActionExecutor(str(tmp_path))

    result = await executor.execute(
        Action(
            id="search-1",
            type="SEARCH",
            payload={"query": "return 42"},
        )
    )

    assert result.success is True
    assert "example.py:2:" in result.stdout


@pytest.mark.asyncio
async def test_edit_rejects_path_traversal(tmp_path):
    outside = tmp_path.parent / "outside_edit.txt"
    outside.write_text("original", encoding="utf-8")
    executor = RealActionExecutor(str(tmp_path))

    result = await executor.execute(
        Action(
            id="edit-traversal-1",
            type="EDIT",
            payload={
                "file": "../outside_edit.txt",
                "old": "original",
                "new": "modified",
            },
        )
    )

    assert result.success is False
    assert outside.read_text(encoding="utf-8") == "original"


@pytest.mark.asyncio
async def test_edit_rejects_ambiguous_replacement(tmp_path):
    target = tmp_path / "example.py"
    original = "return a * b\nreturn a * b\n"
    target.write_text(original, encoding="utf-8")
    executor = RealActionExecutor(str(tmp_path))

    result = await executor.execute(
        Action(
            id="edit-ambiguous-1",
            type="EDIT",
            payload={
                "file": "example.py",
                "old": "return a * b",
                "new": "return a / b",
            },
        )
    )

    assert result.success is False
    assert target.read_text(encoding="utf-8") == original
