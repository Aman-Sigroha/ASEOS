"""Artifacts, records and evidence checks through the real gateway."""

import json

import pytest

from execution.artifacts import ArtifactError, ArtifactStore
from execution.commands.policy import CommandPolicy, CommandRule, Match
from execution.gateway import ExecutionGateway
from execution.gateway.capabilities import CAP_FS_DELETE, CAP_SHELL_RUN, DEFAULT_CAPABILITIES
from execution.gateway.command_tools import RunCommandTool
from execution.gateway.tools import default_tools
from execution.tests.gitfixtures import init_repo, write
from execution.verification import ExecutionVerifier, FileExistsCheck, TestsCheck, run_checks
from runtime.schemas.action import Action
from runtime.schemas.execution import ExecutionResult
from runtime.schemas.task import Task
from runtime.state.state import AgentState

CAPS = DEFAULT_CAPABILITIES | {CAP_SHELL_RUN}
PY_POLICY = CommandPolicy(
    rules=[CommandRule("py", frozenset({"python"}), lambda args: Match("build"))]
)


def tools():
    return [t for t in default_tools() if t.spec.name != "shell.run"] + [
        RunCommandTool(policy=PY_POLICY)
    ]


@pytest.fixture
def repo(tmp_path):
    return init_repo(tmp_path / "repo")


@pytest.fixture
def store(tmp_path):
    return ArtifactStore(tmp_path / "store")


def make(repo, store, caps=CAPS, **kw):
    return ExecutionGateway(
        str(repo), tools=tools(), granted_capabilities=caps, artifact_store=store, **kw
    )


async def act(gateway, action_type, payload, action_id="a-1"):
    return await gateway.execute(Action(id=action_id, type=action_type, payload=payload))


BIG = "print('z' * 300000)"  # bigger than the inline buffers, so the full output is spooled


def py(code):
    return {"command": ["python", "-c", code]}


# ----------------------------------------------------- command output artifacts


@pytest.mark.asyncio
async def test_command_output_is_stored_whole_redacted_and_owned(repo, store):
    gw = make(repo, store)
    code = "print('start'); print('DB_PASSWORD=correct-horse-battery'); print('y' * 400000)"

    result = await act(gw, "RUN_COMMAND", py(code))

    assert result.success and result.truncated
    ref = result.artifacts[0]
    assert ref.kind == "command_output" and ref.sha256 and ref.redactions == 1
    assert "stdout of python" in ref.description and str(repo) not in ref.description
    assert result.data["output_artifacts"] == {"stdout": ref.id}
    assert result.data["output_files"] == {}
    content, meta, _ = store.read(ref.id, workspace=gw.workspace_id, limit=1 << 20)
    assert b"correct-horse" not in content and b"[REDACTED]" in content
    assert content.startswith(b"start") and meta.original_size > 400000
    assert meta.action_id == "a-1" and meta.tool == "shell.run"


@pytest.mark.asyncio
async def test_secrets_never_reach_the_inline_result_either(repo, store):
    gw = make(repo, store)

    result = await act(
        gw, "RUN_COMMAND",
        py("import sys; print('token: abc123def456ghi789'); print('AKIAIOSFODNN7EXAMPLE', file=sys.stderr)"),
    )

    assert "abc123def456" not in result.stdout and "AKIAIOSFODNN7EXAMPLE" not in result.stderr
    assert "[REDACTED]" in result.stdout and "[REDACTED]" in result.stderr


@pytest.mark.asyncio
async def test_raw_spool_files_do_not_linger_on_disk(repo, store):
    gw = make(repo, store)

    await act(gw, "RUN_COMMAND", py("print('secret-ish output ' * 1000)"))
    await act(gw, "RUN_COMMAND", py("import sys; sys.exit(3)"), action_id="a-2")

    assert list(store.spool_dir().iterdir()) == []


@pytest.mark.asyncio
async def test_a_failing_command_keeps_its_output_for_inspection(repo, store):
    gw = make(repo, store)

    result = await act(gw, "RUN_COMMAND", py("import sys; print('about to fail'); print('z' * 300000); sys.exit(2)"))

    assert not result.success and result.exit_code == 2
    content, _, _ = store.read(result.artifacts[0].id, workspace=gw.workspace_id)
    assert content.startswith(b"about to fail")


@pytest.mark.asyncio
async def test_test_runs_store_an_audit_report(repo, store):
    write(repo, "test_x.py", "def test_a(): assert True\ndef test_b(): assert 1 == 2\n")
    gw = make(repo, store)

    result = await act(gw, "RUN_TEST", {"framework": "pytest"})

    assert [a.kind for a in result.artifacts] == ["test_report"]  # console output is small: inline only
    report_ref = next(a for a in result.artifacts if a.kind == "test_report")
    assert report_ref.retention == "audit"
    body, _, _ = store.read(report_ref.id, workspace=gw.workspace_id)
    report = json.loads(body)
    assert report["status"] == "FAILED" and report["counts"]["failed"] == 1
    assert report["workspace_version"]["fingerprint"]


# ------------------------------------------------------------ artifact tools


@pytest.mark.asyncio
async def test_artifact_read_pages_through_a_log_and_reads_the_tail(repo, store):
    gw = make(repo, store)
    produced = await act(gw, "RUN_COMMAND", py("print(''.join(str(i % 10) for i in range(300000)))"))
    art = produced.artifacts[0].id

    first = await act(gw, "READ_ARTIFACT", {"id": art, "limit": 1000}, "r-1")
    second = await act(gw, "READ_ARTIFACT", {"id": art, "offset": first.data["next_offset"], "limit": 1000}, "r-2")
    tail = await act(gw, "READ_ARTIFACT", {"id": art, "limit": 10, "from_end": True}, "r-3")

    assert first.success and first.truncated and first.data["eof"] is False
    assert len(first.stdout) == 1000 and first.data["next_offset"] == 1000
    assert second.data["offset"] == 1000 and second.stdout[0] == "0"
    assert tail.data["eof"] is True and tail.data["next_offset"] is None
    assert tail.stdout.strip().endswith("9") and tail.data["sha256"] == produced.artifacts[0].sha256


@pytest.mark.asyncio
async def test_artifact_read_refuses_unknown_foreign_and_malformed_ids(repo, store, tmp_path):
    other_repo = init_repo(tmp_path / "other")
    mine = make(repo, store)
    theirs = make(other_repo, store)  # same store, different workspace
    produced = await act(theirs, "RUN_COMMAND", py(BIG))

    for bad in (produced.artifacts[0].id, "art_" + "0" * 24, "../../etc/passwd", "x"):
        result = await act(mine, "READ_ARTIFACT", {"id": bad}, "r-1")
        assert not result.success and result.error_category == "INVALID_ARGUMENTS"

    assert (await act(mine, "LIST_ARTIFACTS", {}, "l-1")).data["count"] == 0
    assert (await act(theirs, "LIST_ARTIFACTS", {}, "l-2")).data["count"] >= 1


@pytest.mark.asyncio
async def test_artifact_list_filters_by_kind_and_action(repo, store):
    gw = make(repo, store)
    await act(gw, "RUN_COMMAND", py(BIG), "cmd-A")
    await act(gw, "RUN_COMMAND", py(BIG), "cmd-B")

    by_action = await act(gw, "LIST_ARTIFACTS", {"action_id": "cmd-A", "kind": "command_output"}, "l-1")
    records = await act(gw, "LIST_ARTIFACTS", {"kind": "execution_record"}, "l-2")

    assert by_action.data["count"] == 1
    assert by_action.data["artifacts"][0]["action_id"] == "cmd-A"
    assert records.data["count"] == 2


@pytest.mark.asyncio
async def test_artifact_tools_respect_the_read_capability(repo, store):
    gw = make(repo, store, caps=frozenset({"git.read"}))

    result = await act(gw, "READ_ARTIFACT", {"id": "art_" + "0" * 24})

    assert result.error_category == "PERMISSION_DENIED"


# ------------------------------------------------------------ patches and diffs

PATCH = "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1,2 @@\n one\n+two password = 'in-source'\n"


@pytest.mark.asyncio
async def test_applied_patches_are_kept_as_audit_artifacts(repo, store):
    gw = make(repo, store)

    result = await act(gw, "APPLY_PATCH", {"patch": PATCH})

    assert result.success
    ref = result.artifacts[0]
    assert ref.kind == "patch" and ref.retention == "audit"
    assert result.data["patch_artifact"] == ref.id
    body, meta, _ = store.read(ref.id, workspace=gw.workspace_id)
    assert body.decode() == PATCH and not meta.redacted  # source content is not rewritten
    assert result.data["patch_id"] in meta.description or result.data["patch_id"][:12] in meta.description


@pytest.mark.asyncio
async def test_a_rejected_patch_leaves_failure_evidence(repo, store):
    gw = make(repo, store)
    bad = "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-not the real line\n+x\n"

    result = await act(gw, "APPLY_PATCH", {"patch": bad})

    assert not result.success and result.error_category == "PATCH_CONFLICT"
    ref = result.artifacts[0]
    assert ref.kind == "patch" and "rejected" in ref.description
    assert store.read(ref.id, workspace=gw.workspace_id)[0].decode() == bad
    assert (repo / "a.txt").read_text() == "one\n"


@pytest.mark.asyncio
async def test_a_truncated_diff_has_its_full_text_retrievable(repo, store):
    write(repo, "a.txt", "one\n" + "added line\n" * 400)
    gw = make(repo, store)

    result = await act(gw, "GIT_DIFF", {"mode": "base", "base": "HEAD", "max_bytes": 300})

    assert result.truncated and len(result.stdout) < 400
    ref = next(a for a in result.artifacts if a.kind == "diff")
    full, _, _ = store.read(ref.id, workspace=gw.workspace_id, limit=1 << 20)
    assert full.decode().count("+added line") == 400

    small = await act(gw, "GIT_DIFF", {"mode": "base", "base": "HEAD"}, "a-2")
    assert not small.truncated and small.artifacts == []


# -------------------------------------------------------------------- records


@pytest.mark.asyncio
async def test_state_changing_actions_get_an_audit_record(repo, store):
    gw = make(repo, store)

    result = await act(gw, "APPLY_PATCH", {"patch": PATCH}, "patch-1")

    rid = result.data["execution_record"]
    body, meta, _ = store.read(rid, workspace=gw.workspace_id)
    record = json.loads(body)
    assert meta.kind == "execution_record" and meta.retention == "audit"
    assert record["action_id"] == "patch-1" and record["tool"] == "patch.apply"
    assert record["level"] == "LOW_RISK_MUTATION" and record["success"] is True
    assert record["workspace_id"] == gw.workspace_id
    assert record["links"]["patch_id"] == result.data["patch_id"]
    assert record["artifact_ids"] == [a.id for a in result.artifacts]
    assert record["args"]["patch"] == {"chars": len(PATCH), "sha256": record["args"]["patch"]["sha256"]}
    assert len(record["args_sha256"]) == 64
    assert all(a.kind != "execution_record" for a in result.artifacts)  # outputs only


@pytest.mark.asyncio
async def test_reads_are_not_recorded_unless_asked(repo, store, tmp_path):
    quiet = make(repo, store)
    loud = make(repo, store, record_reads=True)

    a = await act(quiet, "READ", {"path": "a.txt"})
    b = await act(loud, "READ", {"path": "a.txt"})

    assert "execution_record" not in a.data and "execution_record" in b.data


@pytest.mark.asyncio
async def test_records_never_contain_environment_values_or_secrets(repo, store):
    gw = make(repo, store)

    result = await act(
        gw, "RUN_COMMAND",
        {**py("print('hi')"), "env": {"CI": "value-that-must-not-be-recorded"}},
    )

    body, _, _ = store.read(result.data["execution_record"], workspace=gw.workspace_id)
    text = body.decode()
    assert "value-that-must-not-be-recorded" not in text
    assert json.loads(text)["args"]["env"] == {"names": ["CI"]}


@pytest.mark.asyncio
async def test_a_refused_action_is_recorded_too(repo, store):
    gw = make(repo, store, caps=DEFAULT_CAPABILITIES)  # no shell.run

    result = await act(gw, "RUN_COMMAND", py("print(1)"), "denied-1")

    record = json.loads(store.read(result.data["execution_record"], workspace=gw.workspace_id)[0])
    assert record["success"] is False and record["error_category"] == "PERMISSION_DENIED"
    assert record["action_id"] == "denied-1"


@pytest.mark.asyncio
async def test_observers_receive_records_and_cannot_break_the_action(repo, store):
    seen = []
    gw = make(repo, store)
    gw.add_observer(lambda record: (_ for _ in ()).throw(RuntimeError("boom")))
    gw.add_observer(seen.append)

    result = await act(gw, "RUN_COMMAND", py("print('x')"), "obs-1")

    assert result.success
    assert [r["action_id"] for r in seen] == ["obs-1"] and seen[0]["tool"] == "shell.run"


# ------------------------------------------------------ store failure handling


class BrokenStore(ArtifactStore):
    def put(self, *args, **kwargs):
        raise ArtifactError("io", "disk full")

    def put_file(self, *args, **kwargs):
        raise ArtifactError("io", "disk full")


@pytest.mark.asyncio
async def test_a_broken_store_never_fails_the_action_and_is_reported(repo, tmp_path):
    gw = make(repo, BrokenStore(tmp_path / "broken"))

    result = await act(gw, "RUN_COMMAND", py("print('still works'); print('z' * 300000)"))

    assert result.success and "still works" in result.stdout
    assert result.artifacts == []
    errors = result.data["artifact_errors"]
    assert any(e.startswith("command_output") and "disk full" in e for e in errors)
    assert any(e.startswith("execution_record") for e in errors)  # the audit failure is visible too
    assert "execution_record" not in result.data
    assert list(gw.artifact_store.spool_dir().iterdir()) == []  # raw output still removed


@pytest.mark.asyncio
async def test_an_unopenable_store_degrades_to_no_artifacts(repo, tmp_path, monkeypatch):
    from execution.gateway import dispatcher

    def boom(workspace):
        raise ArtifactError("io", "no permission")

    monkeypatch.setattr(dispatcher, "default_store_for", boom)
    gw = ExecutionGateway(str(repo), tools=tools(), granted_capabilities=CAPS)

    result = await act(gw, "RUN_COMMAND", py(BIG))

    assert result.success and gw.artifact_store is None and "no permission" in gw.store_error
    # Without a store the legacy spool-file references remain.
    assert result.data["output_files"] != {} and "output_artifacts" not in result.data
    assert "execution_record" not in result.data


@pytest.mark.asyncio
async def test_artifacts_can_be_turned_off(repo):
    gw = ExecutionGateway(
        str(repo), tools=tools(), granted_capabilities=CAPS, artifact_store=False
    )

    result = await act(gw, "RUN_COMMAND", py("print('x' * 10)"))
    listed = await act(gw, "LIST_ARTIFACTS", {}, "l-1")

    assert result.success and gw.artifact_store is None
    assert "execution_record" not in result.data and "output_artifacts" not in result.data
    assert listed.data["count"] == 0
    assert (await act(gw, "READ_ARTIFACT", {"id": "art_" + "0" * 24}, "r-1")).error_category == "INVALID_ARGUMENTS"


# -------------------------------------------------------- evidence consistency


@pytest.mark.asyncio
async def test_genuine_results_are_consistent(repo, store):
    write(repo, "test_x.py", "def test_a(): assert True\n")
    gw = make(repo, store)

    for action_type, payload in (
        ("RUN_COMMAND", py("print('fine')")),
        ("RUN_COMMAND", py("import sys; sys.exit(4)")),
        ("RUN_COMMAND", py(BIG)),
        ("RUN_TEST", {"framework": "pytest"}),
        ("APPLY_PATCH", {"patch": PATCH}),
    ):
        result = await act(gw, action_type, payload, f"c-{action_type}")
        assert gw.check_evidence(result) == [], (action_type, gw.check_evidence(result))


@pytest.mark.asyncio
async def test_tampered_artifacts_are_caught(repo, store):
    gw = make(repo, store)
    result = await act(gw, "RUN_COMMAND", py(BIG))
    store._blob(result.artifacts[0].id).write_bytes(b"forged")

    problems = gw.check_evidence(result)

    assert problems and "integrity" in problems[0]


@pytest.mark.asyncio
async def test_contradictory_results_are_flagged(repo, store):
    gw = make(repo, store)
    real = await act(gw, "RUN_COMMAND", py("print('x')"))

    claims = {
        "success but non-zero exit": real.model_copy(update={"exit_code": 1}),
        "failure with PASS status": real.model_copy(update={"success": False, "error_category": "PROCESS_FAILURE"}),
        "missing category": real.model_copy(update={"success": False}),
    }

    for label, fake in claims.items():
        assert gw.check_evidence(fake), label


@pytest.mark.asyncio
async def test_an_artifact_that_belongs_to_another_action_or_has_the_wrong_size_is_flagged(repo, store):
    gw = make(repo, store)
    first = await act(gw, "RUN_COMMAND", py(BIG), "act-1")
    second = await act(gw, "RUN_COMMAND", py(BIG), "act-2")

    stolen = second.model_copy(update={"artifacts": first.artifacts})
    resized = first.model_copy(update={"data": {**first.data, "stdout_bytes": 9999}})

    assert any("another action" in p for p in gw.check_evidence(stolen))
    assert any("9999" in p for p in gw.check_evidence(resized))


@pytest.mark.asyncio
async def test_test_counts_that_do_not_add_up_are_flagged(repo, store):
    write(repo, "test_x.py", "def test_a(): assert True\n")
    gw = make(repo, store)
    real = await act(gw, "RUN_TEST", {"framework": "pytest"})
    data = json.loads(json.dumps(real.data))
    data["counts"]["total"] = 7

    problems = gw.check_evidence(real.model_copy(update={"data": data}))

    assert any("do not add up" in p for p in problems)


@pytest.mark.asyncio
async def test_verification_refuses_to_pass_on_inconsistent_evidence(repo, store, monkeypatch):
    write(repo, "test_x.py", "def test_a(): assert True\n")
    gw = make(repo, store)
    ok = await run_checks(gw, [TestsCheck(framework="pytest")])
    assert ok.status == "PASS"

    monkeypatch.setattr(gw, "check_evidence", lambda result: ["artifact abc: integrity"])
    bad = await run_checks(gw, [TestsCheck(framework="pytest"), FileExistsCheck(path="a.txt")])

    assert bad.status == "ERROR"
    assert bad.checks[0].status == "ERROR" and "Inconsistent evidence" in bad.checks[0].message
    assert bad.checks[0].traces[0].evidence_problems == ["artifact abc: integrity"]


@pytest.mark.asyncio
async def test_verification_traces_carry_artifact_references_for_retrieval(repo, store):
    write(repo, "test_x.py", "def test_bad(): assert 1 == 2\n")
    gw = make(repo, store)

    report = await run_checks(gw, [TestsCheck(framework="pytest")])

    trace = report.checks[0].traces[0]
    assert report.status == "FAIL"
    assert [a.kind for a in trace.artifacts] == ["test_report"]
    assert all(a.sha256 for a in trace.artifacts)
