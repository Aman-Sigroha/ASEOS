"""Artifact store, redaction, retention and ownership."""

import os
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from execution.artifacts import (
    ArtifactError,
    ArtifactStore,
    Owner,
    RetentionPolicy,
    default_store_for,
    redact_text,
    workspace_id,
)
from execution.artifacts.store import TRUNCATION_MARKER
from execution.tests.gitfixtures import git, init_repo

ME = Owner(workspace_id="aaaaaaaaaaaaaaaa", task_id="t1", action_id="act-1", tool="shell.run")
OTHER = "bbbbbbbbbbbbbbbb"


@pytest.fixture
def store(tmp_path):
    return ArtifactStore(tmp_path / "store")


def small(tmp_path, **kw):
    return ArtifactStore(tmp_path / "small", RetentionPolicy(**kw))


# ------------------------------------------------------------------ redaction

SECRETS = {
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu\n-----END RSA PRIVATE KEY-----",
    "aws_access_key": "AKIAIOSFODNN7EXAMPLE",
    "github_token": "ghp_" + "a1B2c3D4e5" * 4,
    "slack_token": "xoxb-1234567890-abcdefghijkl",
    "api_key": "sk-" + "Ab1" * 10,
    "google_api_key": "AIza" + "x" * 35,
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpM",
    "bearer": "Authorization: Bearer abcdef1234567890ABCDEF",
    "url_credentials": "postgres://admin:hunter2pass@db.internal:5432/app",
    "assignment": "DB_PASSWORD=correct-horse-battery",
}


@pytest.mark.parametrize("kind", sorted(SECRETS))
def test_each_known_secret_shape_is_masked(kind):
    result = redact_text(f"before {SECRETS[kind]} after")

    assert result.changed and result.kinds.get(kind, 0) >= 1
    assert "[REDACTED]" in result.text
    assert result.text.startswith("before ") and result.text.endswith(" after") or kind in (
        "private_key",
    )
    for fragment in ("hunter2pass", "correct-horse-battery", "AKIAIOSFODNN7EXAMPLE", "MIIBOgIBAAJBAKj34"):
        assert fragment not in result.text


def test_keys_stay_visible_so_output_remains_readable():
    text = redact_text("api_key = 'abcd1234efgh5678' and token: xyz123456789").text

    assert "api_key = [REDACTED]" in text
    assert "token: [REDACTED]" in text
    assert "abcd1234efgh5678" not in text


def test_ordinary_output_is_left_alone():
    sample = (
        "FAILED: 2 passed, 1 failed in 0.3s\nauthor: bob\npassed: 5\n"
        "collected 3 items\nhash 0123456789abcdef0123456789abcdef\nhttps://example.com/path?a=1\n"
    )

    result = redact_text(sample)

    assert not result.changed and result.text == sample


def test_environment_secret_values_are_masked_by_value():
    env = {"MY_API_TOKEN": "s3cr3t-value-123", "PATH": "/usr/bin", "SHORT_SECRET": "abc"}

    result = redact_text("leaked s3cr3t-value-123 twice s3cr3t-value-123 abc", env)

    assert result.text == "leaked [REDACTED] twice [REDACTED] abc"  # short values are not masked
    assert result.kinds["environment_value"] == 2


@pytest.mark.parametrize(
    "hostile",
    [
        "a" * 200_000,
        "-----BEGIN PRIVATE KEY-----" + "A" * 200_000,
        "password" + " " * 100_000 + "=",
        "token=" + "x" * 200_000,
        "Bearer " + "a" * 200_000,
        "eyJ" + "a" * 100_000 + ".eyJ" + "b" * 100_000,
        "://" * 50_000,
        ("a=" * 50_000),
    ],
    ids=["long-word", "open-key", "spaced-name", "long-assignment", "long-bearer",
         "long-jwt", "scheme-spam", "equals-spam"],
)
def test_redaction_is_fast_on_hostile_input(hostile):
    started = time.perf_counter()

    redact_text(hostile, {})

    assert time.perf_counter() - started < 3.0


# ---------------------------------------------------------------------- store


def test_put_and_read_round_trip(store):
    meta = store.put("hello\nworld\n", kind="command_output", owner=ME, description="d")

    chunk, got, eof = store.read(meta.id, workspace=ME.workspace_id)

    assert chunk == b"hello\nworld\n" and eof
    assert got.size == 12 and got.original_size == 12 and not got.stored_truncated
    assert got.workspace_id == ME.workspace_id and got.action_id == "act-1"
    assert got.task_id == "t1" and got.tool == "shell.run"
    assert got.id.startswith("art_") and len(got.id) == 28


def test_ranged_and_tail_reads(store):
    meta = store.put("0123456789" * 10, kind="log", owner=ME)

    first, _, eof1 = store.read(meta.id, workspace=ME.workspace_id, offset=0, limit=15)
    middle, _, eof2 = store.read(meta.id, workspace=ME.workspace_id, offset=15, limit=10)
    tail, _, eof3 = store.read(meta.id, workspace=ME.workspace_id, limit=5, from_end=True)

    assert first == b"012345678901234" and not eof1
    assert middle == b"5678901234" and not eof2
    assert tail == b"56789" and eof3


def test_another_workspace_cannot_tell_the_artifact_exists(store):
    meta = store.put("private", kind="log", owner=ME)

    with pytest.raises(ArtifactError) as foreign:
        store.read(meta.id, workspace=OTHER)
    with pytest.raises(ArtifactError) as missing:
        store.read("art_" + "0" * 24, workspace=OTHER)

    assert foreign.value.kind == missing.value.kind == "not_found"
    assert str(foreign.value) == str(missing.value)
    assert store.find(workspace=OTHER) == []
    assert [m.id for m in store.find(workspace=ME.workspace_id)] == [meta.id]


@pytest.mark.parametrize(
    "bad", ["../x", "art_..", "art_" + "g" * 24, "", "art_" + "0" * 23, "ART_" + "0" * 24, "a/../../etc"]
)
def test_malformed_ids_never_touch_the_filesystem(store, bad):
    with pytest.raises(ArtifactError) as err:
        store.read(bad)

    assert err.value.kind == "invalid"


def test_secrets_are_redacted_before_they_are_stored(store):
    meta = store.put(
        "ok\nDB_PASSWORD=correct-horse-battery\nAKIAIOSFODNN7EXAMPLE\n", kind="command_output", owner=ME
    )

    stored = store._blob(meta.id).read_bytes().decode()

    assert "correct-horse" not in stored and "AKIAIOSFODNN7EXAMPLE" not in stored
    assert meta.redacted and meta.redactions == 2
    assert meta.redaction_kinds == {"aws_access_key": 1, "assignment": 1}


def test_redaction_can_be_skipped_for_workspace_content(store):
    text = "password = 'in the source code'\n"

    meta = store.put(text, kind="patch", owner=ME, redact=False)

    assert store._blob(meta.id).read_text() == text
    assert meta.redacted is False and meta.redactions == 0


def test_oversized_content_keeps_head_and_tail(tmp_path):
    store = small(tmp_path, max_artifact_bytes=1000)
    data = "HEAD" + "x" * 5000 + "TAIL"

    meta = store.put(data, kind="log", owner=ME)
    chunk, _, _ = store.read(meta.id, workspace=ME.workspace_id, limit=5000)

    assert meta.stored_truncated and meta.original_size == len(data) and meta.size <= 1000
    assert chunk.startswith(b"HEAD") and chunk.endswith(b"TAIL")
    assert b"bytes omitted" in chunk


def test_put_file_reads_only_head_and_tail_of_huge_files(tmp_path):
    store = small(tmp_path, max_artifact_bytes=2000)
    source = tmp_path / "big.log"
    source.write_bytes(b"START" + b"." * 1_000_000 + b"END")

    meta = store.put_file(source, kind="command_output", owner=ME)
    chunk, _, _ = store.read(meta.id, workspace=ME.workspace_id, limit=5000)

    assert meta.original_size == 1_000_008 and meta.stored_truncated and meta.size <= 2100
    assert chunk.startswith(b"START") and chunk.endswith(b"END")


def test_integrity_check_detects_tampering(store):
    meta = store.put("evidence", kind="log", owner=ME)
    assert store.verify(meta.id, workspace=ME.workspace_id).sha256 == meta.sha256

    store._blob(meta.id).write_bytes(b"forged!!")

    with pytest.raises(ArtifactError) as err:
        store.verify(meta.id, workspace=ME.workspace_id)

    assert err.value.kind == "integrity"


def test_missing_blob_is_an_integrity_error_not_a_crash(store):
    meta = store.put("x", kind="log", owner=ME)
    store._blob(meta.id).unlink()

    with pytest.raises(ArtifactError) as err:
        store.read(meta.id, workspace=ME.workspace_id)

    assert err.value.kind == "integrity"


# ------------------------------------------------------------------ retention


def test_retention_classes_have_different_lifetimes(tmp_path):
    store = small(tmp_path, temporary_ttl_seconds=100, audit_ttl_seconds=1000)

    tmp = store.put("a", kind="log", owner=ME)
    aud = store.put("b", kind="execution_record", owner=ME, retention="audit")

    def ttl(meta):
        return (
            datetime.fromisoformat(meta.expires_at) - datetime.fromisoformat(meta.created_at)
        ).total_seconds()

    assert round(ttl(tmp)) == 100 and round(ttl(aud)) == 1000
    assert (tmp.retention, aud.retention) == ("temporary", "audit")


def test_expired_artifacts_are_unreadable_and_collected(tmp_path):
    store = small(tmp_path, temporary_ttl_seconds=60, audit_ttl_seconds=3600)
    tmp = store.put("short-lived", kind="log", owner=ME)
    aud = store.put("kept", kind="patch", owner=ME, retention="audit")
    later = time.time() + 120

    report = store.gc(now=later)

    assert report["expired"] == 1 and report["freed_bytes"] == tmp.size
    assert not store._blob(tmp.id).exists() and store._blob(aud.id).exists()


def test_reading_an_expired_artifact_fails_even_before_gc(tmp_path):
    store = small(tmp_path, temporary_ttl_seconds=-1)
    meta = store.put("gone", kind="log", owner=ME)

    with pytest.raises(ArtifactError) as err:
        store.read(meta.id, workspace=ME.workspace_id)

    assert err.value.kind == "not_found"


def test_quota_evicts_oldest_temporary_but_never_audit(tmp_path):
    store = small(tmp_path, max_total_bytes=1000, max_artifact_bytes=400)
    audit = store.put("A" * 300, kind="execution_record", owner=ME, retention="audit")
    old = store.put("B" * 300, kind="log", owner=ME)
    time.sleep(0.01)
    new = store.put("C" * 300, kind="log", owner=ME)

    store.put("D" * 300, kind="log", owner=ME)  # 1200 > 1000: evict

    remaining = {m.id for m in store.find(workspace=ME.workspace_id)}
    assert audit.id in remaining and new.id in remaining and old.id not in remaining


def test_a_store_full_of_audit_records_reports_full_instead_of_deleting_them(tmp_path):
    store = small(tmp_path, max_total_bytes=500, max_artifact_bytes=400)
    first = store.put("A" * 300, kind="patch", owner=ME, retention="audit")

    with pytest.raises(ArtifactError) as err:
        store.put("B" * 300, kind="patch", owner=ME, retention="audit")

    assert err.value.kind == "full"
    assert store.read(first.id, workspace=ME.workspace_id)[0] == b"A" * 300  # untouched


def test_gc_removes_orphans_and_stale_spool_files_only_when_old_enough(tmp_path):
    store = small(tmp_path, orphan_grace_seconds=100, spool_ttl_seconds=100)
    orphan = store._objects / ("art_" + "1" * 24 + ".blob")
    orphan.write_bytes(b"no metadata")
    scratch = store.spool_dir() / "run.stdout.log"
    scratch.write_bytes(b"raw output")

    fresh = store.gc()
    assert orphan.exists() and scratch.exists() and fresh["orphans"] == fresh["spool"] == 0

    old = store.gc(now=time.time() + 500)

    assert not orphan.exists() and not scratch.exists()
    assert old["orphans"] == 1 and old["spool"] == 1


def test_failed_write_leaves_nothing_behind_and_raises_io(store, monkeypatch):
    from execution.artifacts import store as store_module

    real = store_module._write_atomic
    calls = []

    def flaky(path, data):
        calls.append(path.name)
        if path.suffix == ".json":
            raise OSError("disk full")
        real(path, data)

    monkeypatch.setattr(store_module, "_write_atomic", flaky)

    with pytest.raises(ArtifactError) as err:
        store.put("x", kind="log", owner=ME)

    assert err.value.kind == "io"
    assert list(store._objects.iterdir()) == [] and list(store._meta.iterdir()) == []


def test_concurrent_puts_get_distinct_ids_and_all_survive(store):
    ids, errors = [], []

    def worker(n):
        try:
            ids.append(store.put(f"payload {n}", kind="log", owner=ME).id)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(40)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert not errors and len(set(ids)) == 40
    assert len(store.find(workspace=ME.workspace_id, limit=100)) == 40


def test_unwritable_root_is_a_clear_error(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")

    with pytest.raises(ArtifactError) as err:
        ArtifactStore(blocker / "store")

    assert err.value.kind == "io"


# ------------------------------------------------------------------- location


def test_a_git_workspace_gets_its_store_inside_the_git_directory(tmp_path):
    repo = init_repo(tmp_path / "repo")

    store = default_store_for(repo)

    assert store.root == repo / ".git" / "aseos" / "artifacts"
    assert git(repo, "status", "--porcelain").strip() == ""  # invisible to the working tree


def test_linked_worktrees_share_the_repository_store_but_not_each_others_artifacts(tmp_path):
    repo = init_repo(tmp_path / "repo")
    linked = tmp_path / "wt"
    git(repo, "worktree", "add", "-b", "other", str(linked))

    main_store, linked_store = default_store_for(repo), default_store_for(linked)
    meta = main_store.put("main only", kind="log", owner=Owner(workspace_id(repo)))

    assert main_store.root == linked_store.root
    with pytest.raises(ArtifactError):
        linked_store.read(meta.id, workspace=workspace_id(linked))


def test_a_plain_directory_falls_back_to_a_per_workspace_temp_store(tmp_path):
    plain = (tmp_path / "plain").resolve()
    plain.mkdir()

    store = default_store_for(plain)

    assert store.root.name == workspace_id(plain)
    assert str(store.root).startswith(str(__import__("tempfile").gettempdir()))


def test_workspace_ids_are_stable_and_distinct(tmp_path):
    a, b = (tmp_path / "a"), (tmp_path / "b")
    a.mkdir(), b.mkdir()

    assert workspace_id(a) == workspace_id(str(a)) != workspace_id(b)
    assert len(workspace_id(a)) == 16
