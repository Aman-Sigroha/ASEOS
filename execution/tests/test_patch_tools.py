import hashlib
import os
import stat

import pytest

from execution.gateway import ExecutionGateway
from execution.gateway.capabilities import CAP_FS_DELETE
from execution.gateway.tool import ToolError
from execution.gateway.tools import DEFAULT_CAPABILITIES, default_tools
from execution.patches import engine
from execution.patches.engine import apply_plan, plan_patch
from runtime.schemas.action import Action


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    return root


def make_gateway(workspace, capabilities=DEFAULT_CAPABILITIES):
    return ExecutionGateway(
        str(workspace),
        tools=default_tools(),
        granted_capabilities=capabilities,
    )


@pytest.fixture
def gateway(workspace):
    return make_gateway(workspace)


async def run(gateway, action_type, payload, action_id="act-7"):
    return await gateway.execute(
        Action(id=action_id, type=action_type, payload=payload)
    )


def put(workspace, name, text):
    path = workspace / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


CALC = "def divide(a, b):\n    return a * b\n\n\ndef add(a, b):\n    return a + b\n"

CALC_PATCH = """\
--- a/calc.py
+++ b/calc.py
@@ -1,3 +1,3 @@
 def divide(a, b):
-    return a * b
+    return a / b

"""


# --------------------------------------------------------- apply: modify


@pytest.mark.asyncio
async def test_apply_modifies_file_and_reports_actual_result(
    gateway, workspace
):
    target = put(workspace, "calc.py", CALC)

    result = await run(gateway, "APPLY_PATCH", {"patch": CALC_PATCH})

    assert result.success is True, result.stderr
    assert result.tool == "patch.apply"
    assert target.read_text(encoding="utf-8").startswith(
        "def divide(a, b):\n    return a / b\n"
    )
    info = result.data["files"][0]
    assert info["action"] == "modified"
    assert info["sha256_before"] == sha(CALC.encode())
    assert info["sha256_after"] == sha(target.read_bytes())
    assert result.data["changed_files"] == ["calc.py"]
    assert (result.data["additions"], result.data["deletions"]) == (1, 1)
    assert "-    return a * b" in result.data["applied_diff"]
    assert "+    return a / b" in result.data["applied_diff"]


@pytest.mark.asyncio
async def test_apply_records_provenance(gateway, workspace):
    put(workspace, "calc.py", CALC)

    result = await run(
        gateway, "APPLY_PATCH", {"patch": CALC_PATCH}, action_id="act-42"
    )

    assert result.data["action_id"] == "act-42"
    assert result.data["patch_id"] == sha(CALC_PATCH.encode())


@pytest.mark.asyncio
async def test_apply_tolerates_wrong_line_numbers_when_context_is_unique(
    gateway, workspace
):
    put(workspace, "calc.py", "# header\n# more\n" + CALC)
    patch = CALC_PATCH  # claims line 1, real position is line 3

    result = await run(gateway, "APPLY_PATCH", {"patch": patch})

    assert result.success is True, result.stderr
    assert result.data["files"][0]["hunk_offsets"] == [2]


@pytest.mark.asyncio
async def test_apply_rejects_ambiguous_context(gateway, workspace):
    original = "x = 1\nx = 1\n"
    target = put(workspace, "f.py", original)
    patch = "--- a/f.py\n+++ b/f.py\n@@ -9 +9 @@\n-x = 1\n+x = 2\n"

    result = await run(gateway, "APPLY_PATCH", {"patch": patch})

    assert result.error_category == "PATCH_CONFLICT"
    assert "2 places" in result.stderr
    assert target.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_apply_rejects_context_mismatch_and_leaves_file(
    gateway, workspace
):
    target = put(workspace, "calc.py", CALC.replace("a * b", "a ** b"))
    before = target.read_bytes()

    result = await run(gateway, "APPLY_PATCH", {"patch": CALC_PATCH})

    assert result.error_category == "PATCH_CONFLICT"
    assert result.data["hunk"] == 1
    assert "return a ** b" in "".join(result.data["found_lines"])
    assert target.read_bytes() == before


@pytest.mark.asyncio
async def test_apply_preserves_crlf_line_endings(gateway, workspace):
    target = put(workspace, "calc.py", CALC.replace("\n", "\r\n"))

    result = await run(gateway, "APPLY_PATCH", {"patch": CALC_PATCH})

    assert result.success is True, result.stderr
    assert target.read_bytes().startswith(
        b"def divide(a, b):\r\n    return a / b\r\n"
    )
    assert b"\n" not in target.read_bytes().replace(b"\r\n", b"")


@pytest.mark.asyncio
async def test_apply_handles_missing_trailing_newline(gateway, workspace):
    target = put(workspace, "f.txt", "one\ntwo")
    patch = (
        "--- a/f.txt\n+++ b/f.txt\n@@ -1,2 +1,2 @@\n one\n-two\n"
        "\\ No newline at end of file\n+three\n"
        "\\ No newline at end of file\n"
    )

    result = await run(gateway, "APPLY_PATCH", {"patch": patch})

    assert result.success is True, result.stderr
    assert target.read_bytes() == b"one\nthree"


@pytest.mark.asyncio
async def test_apply_adds_lines_after_unterminated_last_line(
    gateway, workspace
):
    target = put(workspace, "f.txt", "one\ntwo")
    patch = "--- a/f.txt\n+++ b/f.txt\n@@ -2 +2,2 @@\n two\n+three\n"

    result = await run(gateway, "APPLY_PATCH", {"patch": patch})

    assert result.success is True, result.stderr
    assert target.read_bytes() == b"one\ntwo\nthree\n"


@pytest.mark.asyncio
async def test_apply_multiple_hunks(gateway, workspace):
    target = put(
        workspace, "n.txt", "".join(f"line{i}\n" for i in range(1, 21))
    )
    patch = (
        "--- a/n.txt\n+++ b/n.txt\n"
        "@@ -2 +2 @@\n-line2\n+TWO\n"
        "@@ -15 +15 @@\n-line15\n+FIFTEEN\n"
    )

    result = await run(gateway, "APPLY_PATCH", {"patch": patch})

    assert result.success is True, result.stderr
    text = target.read_text(encoding="utf-8")
    assert "TWO\n" in text and "FIFTEEN\n" in text
    assert text.count("\n") == 20


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
@pytest.mark.asyncio
async def test_apply_preserves_file_mode(gateway, workspace):
    target = put(workspace, "calc.py", CALC)
    target.chmod(0o755)

    await run(gateway, "APPLY_PATCH", {"patch": CALC_PATCH})

    assert stat.S_IMODE(target.stat().st_mode) == 0o755


# ---------------------------------------------------------- create/delete


@pytest.mark.asyncio
async def test_apply_creates_new_file_with_parent_dirs(gateway, workspace):
    patch = (
        "--- /dev/null\n+++ b/pkg/sub/new.py\n@@ -0,0 +1,2 @@\n"
        "+x = 1\n+y = 2\n"
    )

    result = await run(gateway, "APPLY_PATCH", {"patch": patch})

    assert result.success is True, result.stderr
    assert (workspace / "pkg" / "sub" / "new.py").read_bytes() == (
        b"x = 1\ny = 2\n"
    )
    assert result.data["files"][0]["action"] == "created"
    assert result.data["files"][0]["sha256_before"] is None


@pytest.mark.asyncio
async def test_apply_create_conflicts_with_existing_file(gateway, workspace):
    put(workspace, "new.py", "existing\n")
    patch = "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+x\n"

    result = await run(gateway, "APPLY_PATCH", {"patch": patch})

    assert result.error_category == "PATCH_CONFLICT"
    assert (workspace / "new.py").read_text(encoding="utf-8") == "existing\n"


DELETE_PATCH = "--- a/old.txt\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n"


@pytest.mark.asyncio
async def test_delete_is_denied_without_fs_delete(gateway, workspace):
    target = put(workspace, "old.txt", "a\nb\n")

    result = await run(gateway, "APPLY_PATCH", {"patch": DELETE_PATCH})

    assert result.error_category == "PERMISSION_DENIED"
    assert "fs.delete" in result.stderr
    assert target.exists()


@pytest.mark.asyncio
async def test_delete_works_with_fs_delete(workspace):
    target = put(workspace, "old.txt", "a\nb\n")
    gateway = make_gateway(workspace, DEFAULT_CAPABILITIES | {CAP_FS_DELETE})

    result = await run(gateway, "APPLY_PATCH", {"patch": DELETE_PATCH})

    assert result.success is True, result.stderr
    assert not target.exists()
    assert result.data["files"][0]["action"] == "deleted"
    assert result.data["files"][0]["sha256_after"] is None


@pytest.mark.asyncio
async def test_delete_must_cover_whole_file(workspace):
    target = put(workspace, "old.txt", "a\nb\nextra\n")
    gateway = make_gateway(workspace, DEFAULT_CAPABILITIES | {CAP_FS_DELETE})

    result = await run(gateway, "APPLY_PATCH", {"patch": DELETE_PATCH})

    assert result.error_category == "PATCH_CONFLICT"
    assert target.exists()


# ----------------------------------------------- multi-file and rollback


TWO_FILE_PATCH = (
    "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-one\n+ONE\n"
    "--- a/b.txt\n+++ b/b.txt\n@@ -1 +1 @@\n-two\n+TWO\n"
)


@pytest.mark.asyncio
async def test_multi_file_patch_applies_all(gateway, workspace):
    a = put(workspace, "a.txt", "one\n")
    b = put(workspace, "b.txt", "two\n")

    result = await run(gateway, "APPLY_PATCH", {"patch": TWO_FILE_PATCH})

    assert result.success is True, result.stderr
    assert (a.read_text(), b.read_text()) == ("ONE\n", "TWO\n") or (
        a.read_bytes(),
        b.read_bytes(),
    ) == (b"ONE\n", b"TWO\n")
    assert result.data["changed_files"] == ["a.txt", "b.txt"]


@pytest.mark.asyncio
async def test_conflict_in_second_file_writes_nothing(gateway, workspace):
    a = put(workspace, "a.txt", "one\n")
    put(workspace, "b.txt", "different\n")

    result = await run(gateway, "APPLY_PATCH", {"patch": TWO_FILE_PATCH})

    assert result.error_category == "PATCH_CONFLICT"
    assert a.read_bytes() == b"one\n"


@pytest.mark.asyncio
async def test_write_failure_rolls_back_earlier_files(
    gateway, workspace, monkeypatch
):
    a = put(workspace, "a.txt", "one\n")
    put(workspace, "b.txt", "two\n")
    real_write = engine.atomic_write_bytes
    calls = {"n": 0}

    def flaky(target, payload, precondition=None):
        calls["n"] += 1

        if target.name == "b.txt" and payload.startswith(b"TWO"):
            raise OSError("disk full")

        return real_write(target, payload, precondition)

    monkeypatch.setattr(engine, "atomic_write_bytes", flaky)

    result = await run(gateway, "APPLY_PATCH", {"patch": TWO_FILE_PATCH})

    assert result.success is False
    assert result.error_category == "INTERNAL_ERROR"
    assert result.data["rolled_back"] is True
    assert result.data["partial"] is False
    assert a.read_bytes() == b"one\n"
    assert (workspace / "b.txt").read_bytes() == b"two\n"


@pytest.mark.asyncio
async def test_rollback_removes_created_files_and_dirs(
    gateway, workspace, monkeypatch
):
    put(workspace, "b.txt", "two\n")
    patch = (
        "--- /dev/null\n+++ b/new/dir/c.txt\n@@ -0,0 +1 @@\n+c\n"
        "--- a/b.txt\n+++ b/b.txt\n@@ -1 +1 @@\n-two\n+TWO\n"
    )
    real_write = engine.atomic_write_bytes

    def flaky(target, payload, precondition=None):
        if target.name == "b.txt" and payload.startswith(b"TWO"):
            raise OSError("boom")

        return real_write(target, payload, precondition)

    monkeypatch.setattr(engine, "atomic_write_bytes", flaky)

    result = await run(gateway, "APPLY_PATCH", {"patch": patch})

    assert result.data["rolled_back"] is True
    assert not (workspace / "new").exists()
    assert (workspace / "b.txt").read_bytes() == b"two\n"


@pytest.mark.asyncio
async def test_incomplete_rollback_is_reported_honestly(
    gateway, workspace, monkeypatch
):
    a = put(workspace, "a.txt", "one\n")
    put(workspace, "b.txt", "two\n")
    real_write = engine.atomic_write_bytes

    def broken(target, payload, precondition=None):
        if target.name == "b.txt" and payload.startswith(b"TWO"):
            raise OSError("boom")

        if target.name == "a.txt" and payload == b"one\n":
            raise OSError("cannot restore")

        return real_write(target, payload, precondition)

    monkeypatch.setattr(engine, "atomic_write_bytes", broken)

    result = await run(gateway, "APPLY_PATCH", {"patch": TWO_FILE_PATCH})

    assert result.data["partial"] is True
    assert result.data["unrestored_files"] == ["a.txt"]
    assert a.read_bytes() == b"ONE\n"


def test_file_changed_between_validation_and_apply_is_detected(workspace):
    target = put(workspace, "calc.py", CALC)
    plan = plan_patch(workspace.resolve(), CALC_PATCH)
    target.write_bytes(b"someone else was here\n")

    with pytest.raises(ToolError) as info:
        apply_plan(plan)

    assert info.value.category == "PATCH_CONFLICT"
    assert target.read_bytes() == b"someone else was here\n"


# ------------------------------------------------------------ base hashes


@pytest.mark.asyncio
async def test_base_hash_match_and_mismatch(gateway, workspace):
    put(workspace, "calc.py", CALC)

    stale = await run(
        gateway,
        "APPLY_PATCH",
        {"patch": CALC_PATCH, "base": {"calc.py": sha(b"old content")}},
    )
    ok = await run(
        gateway,
        "APPLY_PATCH",
        {"patch": CALC_PATCH, "base": {"calc.py": sha(CALC.encode())}},
    )

    assert stale.error_category == "PATCH_CONFLICT"
    assert stale.data["expected_sha256"] == sha(b"old content")
    assert ok.success is True, ok.stderr


@pytest.mark.asyncio
async def test_base_must_reference_files_in_patch(gateway, workspace):
    put(workspace, "calc.py", CALC)

    result = await run(
        gateway,
        "APPLY_PATCH",
        {"patch": CALC_PATCH, "base": {"other.py": sha(b"x")}},
    )

    assert result.error_category == "INVALID_ARGUMENTS"


@pytest.mark.asyncio
async def test_base_rejects_non_hash_values(gateway, workspace):
    result = await run(
        gateway,
        "APPLY_PATCH",
        {"patch": CALC_PATCH, "base": {"calc.py": "nothex"}},
    )

    assert result.error_category == "INVALID_ARGUMENTS"


# --------------------------------------------------------------- reverse


@pytest.mark.asyncio
async def test_applied_diff_can_be_reversed_to_restore_original(
    gateway, workspace
):
    target = put(workspace, "calc.py", CALC)
    created_patch = (
        "--- /dev/null\n+++ b/extra.txt\n@@ -0,0 +1 @@\n+hello\n"
    )

    first = await run(gateway, "APPLY_PATCH", {"patch": CALC_PATCH})
    second = await run(gateway, "APPLY_PATCH", {"patch": created_patch})
    undo_edit = await run(
        gateway,
        "APPLY_PATCH",
        {"patch": first.data["applied_diff"], "reverse": True},
    )
    # reversing a create is a delete, which needs fs.delete
    denied = await run(
        gateway,
        "APPLY_PATCH",
        {"patch": second.data["applied_diff"], "reverse": True},
    )

    assert undo_edit.success is True, undo_edit.stderr
    assert target.read_bytes() == CALC.encode()
    assert denied.error_category == "PERMISSION_DENIED"
    assert (workspace / "extra.txt").exists()


@pytest.mark.asyncio
async def test_reversing_a_create_with_fs_delete_removes_the_file(workspace):
    gateway = make_gateway(workspace, DEFAULT_CAPABILITIES | {CAP_FS_DELETE})
    created = await run(
        gateway,
        "APPLY_PATCH",
        {"patch": "--- /dev/null\n+++ b/x.txt\n@@ -0,0 +1 @@\n+hi\n"},
    )

    undo = await run(
        gateway,
        "APPLY_PATCH",
        {"patch": created.data["applied_diff"], "reverse": True},
    )

    assert undo.success is True, undo.stderr
    assert not (workspace / "x.txt").exists()


# -------------------------------------------------------------- validate


@pytest.mark.asyncio
async def test_validate_does_not_modify_anything(gateway, workspace):
    target = put(workspace, "calc.py", CALC)
    before = target.read_bytes()

    result = await run(gateway, "VALIDATE_PATCH", {"patch": CALC_PATCH})

    assert result.success is True, result.stderr
    assert result.tool == "patch.validate"
    assert result.data["valid"] is True
    assert result.data["files"][0]["action"] == "modify"
    assert target.read_bytes() == before


@pytest.mark.asyncio
async def test_validate_reports_conflicts_like_apply(gateway, workspace):
    put(workspace, "calc.py", "totally different\n")

    result = await run(gateway, "VALIDATE_PATCH", {"patch": CALC_PATCH})

    assert result.error_category == "PATCH_CONFLICT"


@pytest.mark.asyncio
async def test_validate_needs_only_read_capability(workspace):
    put(workspace, "calc.py", CALC)
    read_only = make_gateway(workspace, {"fs.read"})

    validated = await run(read_only, "VALIDATE_PATCH", {"patch": CALC_PATCH})
    applied = await run(read_only, "APPLY_PATCH", {"patch": CALC_PATCH})

    assert validated.success is True
    assert applied.error_category == "PERMISSION_DENIED"


# ---------------------------------------------------- safety and limits


@pytest.mark.asyncio
async def test_patch_path_escape_is_denied(gateway, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    patch = "--- a/../outside.txt\n+++ b/../outside.txt\n@@ -1 +1 @@\n-keep\n+gone\n"

    result = await run(gateway, "APPLY_PATCH", {"patch": patch})

    assert result.error_category == "PERMISSION_DENIED"
    assert outside.read_text(encoding="utf-8") == "keep"


@pytest.mark.asyncio
async def test_patch_blocks_git_internals_and_env_files(gateway, workspace):
    put(workspace, ".env", "SECRET=1\n")
    git_patch = "--- /dev/null\n+++ b/.git/hooks/pre-commit\n@@ -0,0 +1 @@\n+x\n"
    env_patch = "--- a/.env\n+++ b/.env\n@@ -1 +1 @@\n-SECRET=1\n+SECRET=2\n"

    git = await run(gateway, "APPLY_PATCH", {"patch": git_patch})
    env = await run(gateway, "APPLY_PATCH", {"patch": env_patch})

    assert git.error_category == "PERMISSION_DENIED"
    assert env.error_category == "PERMISSION_DENIED"
    assert (workspace / ".env").read_text() == "SECRET=1\n" or (
        workspace / ".env"
    ).read_bytes() == b"SECRET=1\n"


@pytest.mark.asyncio
async def test_env_example_files_are_patchable(gateway, workspace):
    put(workspace, ".env.example", "KEY=\n")
    patch = "--- a/.env.example\n+++ b/.env.example\n@@ -1 +1 @@\n-KEY=\n+KEY=changeme\n"

    result = await run(gateway, "APPLY_PATCH", {"patch": patch})

    assert result.success is True, result.stderr


@pytest.mark.asyncio
async def test_missing_target_file_is_a_conflict(gateway):
    result = await run(gateway, "APPLY_PATCH", {"patch": CALC_PATCH})

    assert result.error_category == "PATCH_CONFLICT"
    assert "does not exist" in result.stderr


@pytest.mark.asyncio
async def test_invalid_patch_text_is_invalid_arguments(gateway):
    result = await run(gateway, "APPLY_PATCH", {"patch": "not a diff"})

    assert result.error_category == "INVALID_ARGUMENTS"
    assert "Invalid patch" in result.stderr


@pytest.mark.asyncio
async def test_oversized_patch_is_rejected(gateway, monkeypatch):
    monkeypatch.setattr(engine, "MAX_PATCH_BYTES", 50)

    result = await run(gateway, "APPLY_PATCH", {"patch": CALC_PATCH})

    assert result.error_category == "RESOURCE_LIMIT"


@pytest.mark.asyncio
async def test_too_many_files_is_rejected(gateway, monkeypatch):
    monkeypatch.setattr(engine, "MAX_PATCH_FILES", 1)

    result = await run(gateway, "APPLY_PATCH", {"patch": TWO_FILE_PATCH})

    assert result.error_category == "RESOURCE_LIMIT"


@pytest.mark.asyncio
async def test_duplicate_file_entries_are_rejected(gateway, workspace):
    put(workspace, "a.txt", "one\n")
    patch = (
        "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-one\n+two\n"
        "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-two\n+three\n"
    )

    result = await run(gateway, "APPLY_PATCH", {"patch": patch})

    assert result.error_category == "INVALID_ARGUMENTS"
    assert (workspace / "a.txt").read_bytes() == b"one\n"


@pytest.mark.asyncio
async def test_binary_target_is_rejected(gateway, workspace):
    target = workspace / "img.bin"
    target.write_bytes(b"\x00\x01\x02")
    patch = "--- a/img.bin\n+++ b/img.bin\n@@ -1 +1 @@\n-x\n+y\n"

    result = await run(gateway, "APPLY_PATCH", {"patch": patch})

    assert result.error_category == "INVALID_ARGUMENTS"
    assert "binary" in result.stderr


@pytest.mark.asyncio
async def test_no_temp_files_left_behind(gateway, workspace):
    put(workspace, "calc.py", CALC)

    await run(gateway, "APPLY_PATCH", {"patch": CALC_PATCH})

    assert [p.name for p in workspace.iterdir()] == ["calc.py"]
