"""Evidence consistency: does a result agree with itself and with its artifacts?

Each rule compares two independent pieces of the same operation (exit code
vs success, counts vs status, artifact hash vs the stored bytes, artifact
size vs the byte count the process reported). A non-empty list means the
evidence must not be trusted as it stands.
"""

from execution.artifacts.sink import ArtifactSink, NullSink
from execution.artifacts.store import ArtifactError
from runtime.schemas.execution import ExecutionResult


def _command_rules(result: ExecutionResult) -> list[str]:
    problems: list[str] = []
    status = result.data.get("status")

    if result.success and result.exit_code not in (0, None):
        problems.append(f"success with exit code {result.exit_code}")

    if result.success and status not in (None, "PASS"):
        problems.append(f"success but process status is {status}")

    if not result.success and status == "PASS":
        problems.append("failure but the process status is PASS")

    if status == "FAIL" and result.exit_code in (0, None):
        problems.append("process status FAIL without a non-zero exit code")

    if status == "TIMEOUT" and result.error_category != "TIMEOUT":
        problems.append("process timed out but the error category is not TIMEOUT")

    return problems


def _test_rules(result: ExecutionResult) -> list[str]:
    problems: list[str] = []
    data = result.data
    status = data.get("status")
    counts = data.get("counts")

    if status is None:
        return problems

    if result.success != (status == "PASSED"):
        problems.append(f"success={result.success} but test status is {status}")

    if counts:
        parts = sum(counts.get(k, 0) for k in ("passed", "failed", "errors", "skipped"))

        if counts.get("total", 0) != parts:
            problems.append("test counts do not add up to the total")

        if status == "PASSED" and (counts.get("passed", 0) < 1 or counts.get("failed") or counts.get("errors")):
            problems.append("PASSED with no passing test or with failures")

        if status == "FAILED" and not (counts.get("failed") or counts.get("errors")):
            problems.append("FAILED without any failing test")

    elif status in ("PASSED", "FAILED"):
        problems.append(f"{status} without readable counts")

    return problems


def check_result(result: ExecutionResult, sink: ArtifactSink | NullSink) -> list[str]:
    """Problems found in `result` (empty when it is consistent)."""

    problems: list[str] = []

    if not result.success and result.error_category is None:
        problems.append("failure without an error category")

    if result.tool in ("shell.run",):
        problems += _command_rules(result)
    elif result.tool == "test.run":
        problems += _test_rules(result)
    elif result.tool == "patch.apply" and result.success:
        if not result.data.get("patch_id") or not result.data.get("files"):
            problems.append("patch applied without a patch id or file list")

    spooled = {
        "stdout": result.data.get("stdout_bytes"),
        "stderr": result.data.get("stderr_bytes"),
    }

    for ref in result.artifacts:
        if ref.size is None:
            continue  # legacy reference, not held by the store

        try:
            meta = sink.verify(ref.id)
        except ArtifactError as exc:
            problems.append(f"artifact {ref.id}: {exc.kind}")
            continue

        if meta.action_id and meta.action_id != result.action_id:
            problems.append(f"artifact {ref.id} belongs to another action")

        if meta.sha256 != ref.sha256 or meta.size != ref.size:
            problems.append(f"artifact {ref.id} differs from the reference in the result")

        stream = meta.description.split(" ", 1)[0]
        expected = spooled.get(stream) if meta.kind == "command_output" else None

        if (
            expected is not None
            and not result.data.get("spool_truncated")
            and not meta.stored_truncated
            and meta.original_size != expected
        ):
            problems.append(
                f"artifact {ref.id} holds {meta.original_size} bytes of {stream} "
                f"but the process produced {expected}"
            )

    return problems
