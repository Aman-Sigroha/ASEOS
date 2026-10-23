import hashlib
import os
import subprocess

import pytest

from execution.gateway import ExecutionGateway
from execution.gateway.fs_tools import MAX_READ_BYTES, MAX_WRITE_BYTES
from execution.gateway.tools import DEFAULT_CAPABILITIES, default_tools
from runtime.schemas.action import Action


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    return root


@pytest.fixture
def gateway(workspace):
    return ExecutionGateway(
        str(workspace),
        tools=default_tools(),
        granted_capabilities=DEFAULT_CAPABILITIES,
    )


async def run(gateway, action_type, payload=None):
    return await gateway.execute(
        Action(id="a-1", type=action_type, payload=payload or {})
    )


def make_junction(link, target):
    """Directory junction: Windows' unprivileged directory link."""
    if os.name != "nt":
        pytest.skip("junctions are Windows-only")

    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
    )

    if completed.returncode != 0:
        pytest.skip("could not create a junction")


def make_symlink(link, target):
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available on this machine")


# ------------------------------------------------------------------ READ


@pytest.mark.asyncio
async def test_read_returns_sha256_and_counts(gateway, workspace):
    (workspace / "f.txt").write_bytes(b"a\nb\nc\n")

    result = await run(gateway, "READ", {"path": "f.txt"})

    assert result.success is True
    assert result.stdout == "a\nb\nc\n"
    assert result.data["sha256"] == sha("a\nb\nc\n")
    assert result.data["total_lines"] == 3
    assert result.truncated is False


@pytest.mark.asyncio
async def test_read_line_range(gateway, workspace):
    (workspace / "f.txt").write_bytes(b"1\n2\n3\n4\n5\n")

    result = await run(
        gateway, "READ", {"path": "f.txt", "start_line": 2, "end_line": 4}
    )

    assert result.stdout == "2\n3\n4\n"
    assert result.data["start_line"] == 2
    assert result.data["end_line"] == 4


@pytest.mark.asyncio
async def test_read_end_line_is_clamped_and_start_beyond_eof_fails(
    gateway, workspace
):
    (workspace / "f.txt").write_bytes(b"1\n2\n")

    clamped = await run(
        gateway, "READ", {"path": "f.txt", "start_line": 2, "end_line": 99}
    )
    beyond = await run(gateway, "READ", {"path": "f.txt", "start_line": 5})

    assert clamped.stdout == "2\n"
    assert clamped.data["end_line"] == 2
    assert beyond.error_category == "INVALID_ARGUMENTS"


@pytest.mark.asyncio
async def test_read_rejects_inverted_range(gateway, workspace):
    (workspace / "f.txt").write_bytes(b"1\n2\n")

    result = await run(
        gateway, "READ", {"path": "f.txt", "start_line": 3, "end_line": 1}
    )

    assert result.error_category == "INVALID_ARGUMENTS"


@pytest.mark.asyncio
async def test_read_truncates_large_output(gateway, workspace):
    (workspace / "big.txt").write_text(
        "x" * (MAX_READ_BYTES + 500), encoding="utf-8"
    )

    result = await run(gateway, "READ", {"path": "big.txt"})

    assert result.success is True
    assert result.truncated is True
    assert len(result.stdout.encode("utf-8")) <= MAX_READ_BYTES
    assert result.data["total_bytes"] == MAX_READ_BYTES + 500


@pytest.mark.asyncio
async def test_read_respects_smaller_max_bytes(gateway, workspace):
    (workspace / "f.txt").write_text("abcdefghij", encoding="utf-8")

    result = await run(gateway, "READ", {"path": "f.txt", "max_bytes": 4})

    assert result.stdout == "abcd"
    assert result.truncated is True


@pytest.mark.asyncio
async def test_read_cannot_raise_the_byte_limit(gateway, workspace):
    (workspace / "f.txt").write_text("x", encoding="utf-8")

    result = await run(
        gateway, "READ", {"path": "f.txt", "max_bytes": MAX_READ_BYTES + 1}
    )

    assert result.error_category == "INVALID_ARGUMENTS"


@pytest.mark.asyncio
async def test_read_refuses_files_over_hard_limit(
    gateway, workspace, monkeypatch
):
    monkeypatch.setattr("execution.gateway.fs_tools.HARD_READ_BYTES", 10)
    (workspace / "f.txt").write_text("x" * 11, encoding="utf-8")

    result = await run(gateway, "READ", {"path": "f.txt"})

    assert result.error_category == "RESOURCE_LIMIT"


@pytest.mark.asyncio
async def test_read_rejects_binary_files(gateway, workspace):
    (workspace / "img.bin").write_bytes(b"\x89PNG\x00\x00data")

    result = await run(gateway, "READ", {"path": "img.bin"})

    assert result.error_category == "INVALID_ARGUMENTS"
    assert "binary" in result.stderr


# -------------------------------------------------------------- LIST_DIR


@pytest.mark.asyncio
async def test_list_dir_sorts_dirs_first(gateway, workspace):
    (workspace / "b.txt").write_text("bb", encoding="utf-8")
    (workspace / "a.txt").write_text("a", encoding="utf-8")
    (workspace / "zdir").mkdir()

    result = await run(gateway, "LIST_DIR", {"path": "."})

    names = [e["name"] for e in result.data["entries"]]
    assert names == ["zdir", "a.txt", "b.txt"]
    assert result.data["entries"][1]["size"] == 1


@pytest.mark.asyncio
async def test_list_dir_truncates_and_rejects_files(gateway, workspace):
    for i in range(5):
        (workspace / f"f{i}.txt").write_text("x", encoding="utf-8")

    truncated = await run(
        gateway, "LIST_DIR", {"path": ".", "max_entries": 2}
    )
    not_dir = await run(gateway, "LIST_DIR", {"path": "f0.txt"})

    assert truncated.truncated is True
    assert truncated.data["entry_count"] == 2
    assert not_dir.error_category == "INVALID_ARGUMENTS"


@pytest.mark.asyncio
async def test_list_dir_blocks_escape(gateway):
    result = await run(gateway, "LIST_DIR", {"path": ".."})

    assert result.error_category == "PERMISSION_DENIED"


# ------------------------------------------------------------- FILE_INFO


@pytest.mark.asyncio
async def test_file_info_for_existing_and_missing(gateway, workspace):
    (workspace / "f.txt").write_text("hello", encoding="utf-8")

    present = await run(gateway, "FILE_INFO", {"path": "f.txt"})
    missing = await run(gateway, "FILE_INFO", {"path": "nope.txt"})

    assert present.data["exists"] is True
    assert present.data["type"] == "file"
    assert present.data["size"] == 5
    assert present.data["sha256"] == sha("hello")
    assert present.data["binary"] is False
    assert missing.success is True
    assert missing.data["exists"] is False


@pytest.mark.asyncio
async def test_file_info_blocks_escape(gateway):
    result = await run(gateway, "FILE_INFO", {"path": "../outside"})

    assert result.error_category == "PERMISSION_DENIED"


# --------------------------------------------------------------- SYMLINKS


@pytest.mark.asyncio
async def test_read_follows_symlink_inside_workspace(gateway, workspace):
    (workspace / "real.txt").write_text("data", encoding="utf-8")
    make_symlink(workspace / "link.txt", workspace / "real.txt")

    result = await run(gateway, "READ", {"path": "link.txt"})

    assert result.success is True
    assert result.stdout == "data"


@pytest.mark.asyncio
async def test_read_blocks_symlink_escaping_workspace(
    gateway, workspace, tmp_path
):
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    make_symlink(workspace / "link.txt", tmp_path / "secret.txt")

    result = await run(gateway, "READ", {"path": "link.txt"})

    assert result.error_category == "PERMISSION_DENIED"
    assert "secret" not in result.stdout


@pytest.mark.asyncio
async def test_list_dir_hides_symlinks_that_escape(
    gateway, workspace, tmp_path
):
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    (workspace / "ok.txt").write_text("ok", encoding="utf-8")
    make_symlink(workspace / "link.txt", tmp_path / "secret.txt")

    result = await run(gateway, "LIST_DIR", {"path": "."})

    assert [e["name"] for e in result.data["entries"]] == ["ok.txt"]


@pytest.mark.asyncio
async def test_junction_escaping_workspace_is_blocked(
    gateway, workspace, tmp_path
):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    make_junction(workspace / "jlink", outside)

    read = await run(gateway, "READ", {"path": "jlink/secret.txt"})
    write = await run(
        gateway, "WRITE", {"path": "jlink/new.txt", "content": "x"}
    )
    listing = await run(gateway, "LIST_DIR", {"path": "jlink"})

    assert read.error_category == "PERMISSION_DENIED"
    assert write.error_category == "PERMISSION_DENIED"
    assert listing.error_category == "PERMISSION_DENIED"
    assert not (outside / "new.txt").exists()


@pytest.mark.asyncio
async def test_write_refuses_to_write_through_symlink(
    gateway, workspace
):
    (workspace / "real.txt").write_text("orig", encoding="utf-8")
    make_symlink(workspace / "link.txt", workspace / "real.txt")

    result = await run(
        gateway,
        "WRITE",
        {
            "path": "link.txt",
            "content": "new",
            "expected_sha256": sha("orig"),
        },
    )

    assert result.error_category == "PERMISSION_DENIED"
    assert (workspace / "real.txt").read_text(encoding="utf-8") == "orig"


# ----------------------------------------------------------------- WRITE


@pytest.mark.asyncio
async def test_write_creates_new_file_and_reports_hash(gateway, workspace):
    result = await run(
        gateway, "WRITE", {"path": "src/new.py", "content": "x = 1\n"}
    )

    assert result.success is True
    assert (workspace / "src" / "new.py").read_text(
        encoding="utf-8"
    ) == "x = 1\n"
    assert result.data["created"] is True
    assert result.data["sha256"] == sha("x = 1\n")
    assert result.data["changed_files"] == ["src/new.py"]
    assert list((workspace / "src").glob("*.tmp")) == []


@pytest.mark.asyncio
async def test_write_requires_expected_hash_to_overwrite(gateway, workspace):
    (workspace / "f.txt").write_text("old", encoding="utf-8")

    result = await run(
        gateway, "WRITE", {"path": "f.txt", "content": "new"}
    )

    assert result.error_category == "INVALID_ARGUMENTS"
    assert result.data["actual_sha256"] == sha("old")
    assert (workspace / "f.txt").read_text(encoding="utf-8") == "old"


@pytest.mark.asyncio
async def test_write_overwrites_when_hash_matches(gateway, workspace):
    (workspace / "f.txt").write_text("old", encoding="utf-8")

    result = await run(
        gateway,
        "WRITE",
        {"path": "f.txt", "content": "new", "expected_sha256": sha("old")},
    )

    assert result.success is True
    assert result.data["previous_sha256"] == sha("old")
    assert result.data["created"] is False
    assert (workspace / "f.txt").read_text(encoding="utf-8") == "new"


@pytest.mark.asyncio
async def test_write_conflict_on_stale_hash_leaves_file_untouched(
    gateway, workspace
):
    (workspace / "f.txt").write_text("changed by someone", encoding="utf-8")

    result = await run(
        gateway,
        "WRITE",
        {"path": "f.txt", "content": "new", "expected_sha256": sha("old")},
    )

    assert result.error_category == "PATCH_CONFLICT"
    assert result.data["expected_sha256"] == sha("old")
    assert result.data["actual_sha256"] == sha("changed by someone")
    assert (workspace / "f.txt").read_text(
        encoding="utf-8"
    ) == "changed by someone"


@pytest.mark.asyncio
async def test_write_conflict_when_expected_hash_but_file_missing(
    gateway, workspace
):
    result = await run(
        gateway,
        "WRITE",
        {"path": "gone.txt", "content": "x", "expected_sha256": sha("old")},
    )

    assert result.error_category == "PATCH_CONFLICT"
    assert not (workspace / "gone.txt").exists()


@pytest.mark.asyncio
async def test_write_rejects_bad_hash_format(gateway):
    result = await run(
        gateway,
        "WRITE",
        {"path": "f.txt", "content": "x", "expected_sha256": "abc"},
    )

    assert result.error_category == "INVALID_ARGUMENTS"


@pytest.mark.asyncio
async def test_write_blocks_path_escape(gateway, tmp_path):
    result = await run(
        gateway, "WRITE", {"path": "../outside.txt", "content": "x"}
    )

    assert result.error_category == "PERMISSION_DENIED"
    assert not (tmp_path / "outside.txt").exists()


@pytest.mark.asyncio
async def test_write_blocks_git_internals(gateway, workspace):
    result = await run(
        gateway, "WRITE", {"path": ".git/hooks/pre-commit", "content": "x"}
    )

    assert result.error_category == "PERMISSION_DENIED"
    assert not (workspace / ".git").exists()


@pytest.mark.asyncio
async def test_edit_blocks_git_internals(gateway, workspace):
    (workspace / ".git").mkdir()
    (workspace / ".git" / "config").write_text("a", encoding="utf-8")

    result = await run(
        gateway,
        "EDIT",
        {"file": ".git/config", "old": "a", "new": "b"},
    )

    assert result.error_category == "PERMISSION_DENIED"
    assert (workspace / ".git" / "config").read_text(
        encoding="utf-8"
    ) == "a"


@pytest.mark.asyncio
async def test_write_rejects_directory_and_oversized_content(
    gateway, workspace
):
    (workspace / "d").mkdir()

    directory = await run(gateway, "WRITE", {"path": "d", "content": "x"})
    too_big = await run(
        gateway,
        "WRITE",
        {"path": "big.txt", "content": "x" * (MAX_WRITE_BYTES + 1)},
    )

    assert directory.error_category == "INVALID_ARGUMENTS"
    assert too_big.error_category == "RESOURCE_LIMIT"
    assert not (workspace / "big.txt").exists()


@pytest.mark.asyncio
async def test_write_preserves_crlf_exactly(gateway, workspace):
    result = await run(
        gateway, "WRITE", {"path": "w.txt", "content": "a\r\nb\r\n"}
    )

    assert result.success is True
    assert (workspace / "w.txt").read_bytes() == b"a\r\nb\r\n"


@pytest.mark.asyncio
async def test_write_requires_fs_write_capability(workspace):
    read_only = ExecutionGateway(
        str(workspace),
        tools=default_tools(),
        granted_capabilities={"fs.read"},
    )

    result = await run(
        read_only, "WRITE", {"path": "f.txt", "content": "x"}
    )

    assert result.error_category == "PERMISSION_DENIED"
    assert not (workspace / "f.txt").exists()


@pytest.mark.asyncio
async def test_read_then_write_round_trip(gateway, workspace):
    (workspace / "f.txt").write_text("v1", encoding="utf-8")

    read = await run(gateway, "READ", {"path": "f.txt"})
    write = await run(
        gateway,
        "WRITE",
        {
            "path": "f.txt",
            "content": "v2",
            "expected_sha256": read.data["sha256"],
        },
    )
    stale = await run(
        gateway,
        "WRITE",
        {
            "path": "f.txt",
            "content": "v3",
            "expected_sha256": read.data["sha256"],
        },
    )

    assert write.success is True
    assert stale.error_category == "PATCH_CONFLICT"
