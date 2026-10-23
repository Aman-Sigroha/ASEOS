"""Run verification checks through the execution gateway.

Every check is established by gateway tool calls (so capabilities, the
command policy, path rules and the sandbox all apply) and recorded as
evidence. Nothing here accepts "the plan finished" or "the model says it
works" as a result.
"""

import asyncio
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from execution.gateway.dispatcher import ExecutionGateway
from execution.verification.checks import (
    Check,
    CommandCheck,
    DiffContainsCheck,
    FileContentCheck,
    FileExistsCheck,
    NoProhibitedPathsCheck,
    TestsCheck,
    describe,
)
from execution.verification.evidence import (
    CheckEvidence,
    NextStep,
    ToolTrace,
    VerificationReport,
    excerpt,
)
from execution.verification.globs import GlobSet
from execution.versioning import workspace_version
from runtime.schemas.action import Action
from runtime.schemas.execution import ExecutionResult
from runtime.verification.result import VerificationStatus

# Failures where the check could not even be evaluated, as opposed to
# evaluated and not satisfied. Retrying the same thing will not help.
_BLOCKED_CATEGORIES = frozenset(
    {
        "PERMISSION_DENIED",
        "UNKNOWN_TOOL",
        "INVALID_ARGUMENTS",
        "WORKSPACE_UNAVAILABLE",
        "RESOURCE_LIMIT",
    }
)
_BLOCKED_NOT_RUN = frozenset({"no_framework", "policy", "sandbox", "not_installed"})
_TEST_FAIL_STATUSES = frozenset({"FAILED", "ERROR", "NO_TESTS"})
MAX_LISTED = 50
_NOT_INSTALLED = re.compile(r"No module named '?([\w.]+)")


def fold_status(statuses: list[VerificationStatus]) -> VerificationStatus:
    """TIMEOUT > ERROR > FAIL > PASS. An empty list is not a pass."""

    if not statuses:
        return "ERROR"

    for candidate in ("TIMEOUT", "ERROR", "FAIL"):
        if candidate in statuses:
            return candidate  # type: ignore[return-value]

    return "PASS"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short(text: str, limit: int = 80) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


def _trace(result: ExecutionResult, *, output: bool) -> ToolTrace:
    return ToolTrace(
        tool=result.tool,
        action_id=result.action_id,
        success=result.success,
        exit_code=result.exit_code,
        error_category=result.error_category,
        duration_ms=result.duration_ms,
        truncated=result.truncated,
        artifacts=list(result.artifacts),
        stdout_excerpt=excerpt(result.stdout, 1500) if output else "",
        stderr_excerpt=excerpt(result.stderr, 1500) if output else "",
    )


def _steps(
    status: VerificationStatus,
    *,
    blocked: bool = False,
    failing_tests: bool = False,
    more_output: bool = False,
) -> list[NextStep]:
    if status == "PASS":
        return []

    if blocked:
        return ["replan", "stop"]

    steps: list[NextStep] = ["inspect"]

    if more_output:
        steps.append("retrieve_output")

    if status == "TIMEOUT":
        steps += ["retest_targeted", "replan"]
    elif status == "FAIL":
        if failing_tests:
            steps.append("retest_targeted")

        steps += ["modify_patch", "replan"]
    else:  # ERROR: the check itself could not be established
        steps += ["replan", "stop"]

    return steps


class _Run:
    """State shared by the checks of one verification run."""

    def __init__(self, gateway: ExecutionGateway, run_id: str) -> None:
        self.gateway = gateway
        self.run_id = run_id
        self.index = 0
        self._seq = 0

    async def call(
        self, action_type: str, payload: dict[str, Any], *, output: bool = False
    ) -> tuple[ExecutionResult, ToolTrace]:
        self._seq += 1
        action = Action(
            id=f"verify-{self.run_id}-{self.index}-{self._seq}",
            type=action_type,  # type: ignore[arg-type]
            payload=payload,
        )
        result = await self.gateway.execute(action)
        return result, _trace(result, output=output)


def _evidence(
    run: _Run,
    check: Check,
    status: VerificationStatus,
    message: str,
    started: float,
    *,
    observed: dict[str, Any] | None = None,
    traces: list[ToolTrace] | None = None,
    next_steps: list[NextStep] | None = None,
) -> CheckEvidence:
    return CheckEvidence(
        index=run.index,
        kind=check.kind,
        name=describe(check),
        status=status,
        message=message,
        duration_ms=int((time.perf_counter() - started) * 1000),
        observed=observed or {},
        traces=traces or [],
        next_steps=next_steps if next_steps is not None else _steps(status),
    )


def _cannot_evaluate(
    run: _Run, check: Check, result: ExecutionResult, trace: ToolTrace, started: float
) -> CheckEvidence:
    """A supporting tool call failed, so the condition was never evaluated."""

    blocked = result.error_category in _BLOCKED_CATEGORIES
    reason = (result.stderr or result.stdout or "tool call failed").strip()
    return _evidence(
        run, check, "ERROR",
        f"{result.tool or 'tool'} failed ({result.error_category}): {excerpt(reason, 600)}",
        started,
        observed={"error_category": result.error_category},
        traces=[trace],
        next_steps=_steps("ERROR", blocked=blocked),
    )


# ------------------------------------------------------------------ tests


async def _check_tests(run: _Run, check: TestsCheck, started: float) -> CheckEvidence:
    payload: dict[str, Any] = {"framework": check.framework, "targets": check.targets}

    if check.keyword:
        payload["keyword"] = check.keyword

    if check.timeout_seconds:
        payload["timeout_seconds"] = check.timeout_seconds

    result, trace = await run.call("RUN_TEST", payload, output=True)
    data = result.data
    test_status = data.get("status") if result.tool == "test.run" else None

    if result.success:
        counts = data.get("counts") or {}
        return _evidence(
            run, check, "PASS", str(data.get("summary") or "All tests passed."), started,
            observed={
                "test_status": test_status,
                "counts": counts,
                "scope": data.get("scope"),
                "framework": data.get("framework"),
                "targets": data.get("targets", []),
                "report_source": data.get("report_source"),
                "workspace_version": data.get("workspace_version"),
                "modified_during_run": data.get("modified_during_run", False),
            },
            traces=[trace],
        )

    if test_status is None:
        return _cannot_evaluate(run, check, result, trace, started)

    failing = [
        {
            "name": case.get("name"),
            "classname": case.get("classname"),
            "status": case.get("status"),
            "message": excerpt(str(case.get("message") or ""), 300),
        }
        for case in (data.get("cases") or [])
        if case.get("status") in ("failed", "error")
    ][:20]
    not_run_kind = data.get("not_run_kind")
    status: VerificationStatus
    blocked = False

    if test_status in _TEST_FAIL_STATUSES:
        status = "FAIL"
    elif test_status == "TIMEOUT":
        status = "TIMEOUT"
    else:  # UNVERIFIED, CANCELLED, NOT_RUN
        status = "ERROR"
        blocked = test_status == "NOT_RUN" and (
            not_run_kind in _BLOCKED_NOT_RUN or result.error_category in _BLOCKED_CATEGORIES
        )

    more = bool(result.artifacts or result.truncated or data.get("cases_truncated"))
    headline = str(data.get("summary") or result.stderr or "tests did not pass")
    details = "".join(
        f"\n- {case['name']}: {excerpt(case['message'], 200)}".rstrip(": ")
        for case in failing[:3]
    )
    return _evidence(
        run, check, status,
        excerpt(headline, 400) + details,
        started,
        observed={
            "test_status": test_status,
            "counts": data.get("counts"),
            "scope": data.get("scope"),
            "framework": data.get("framework"),
            "targets": data.get("targets", []),
            "failing_cases": failing,
            "report_problem": data.get("report_problem"),
            "not_run_kind": not_run_kind,
            "workspace_version": data.get("workspace_version"),
            "modified_during_run": data.get("modified_during_run", False),
        },
        traces=[trace],
        next_steps=_steps(
            status, blocked=blocked, failing_tests=bool(failing), more_output=more
        ),
    )


# ---------------------------------------------------- build / lint / typecheck


async def _check_command(run: _Run, check: CommandCheck, started: float) -> CheckEvidence:
    payload: dict[str, Any] = {"command": check.command, "cwd": check.cwd}

    if check.timeout_seconds:
        payload["timeout_seconds"] = check.timeout_seconds

    result, trace = await run.call("RUN_COMMAND", payload, output=True)
    data = result.data
    policy = data.get("policy") or {}
    category = policy.get("category")
    process_status = data.get("status")
    more = bool(result.artifacts or result.truncated)
    observed = {
        "command": data.get("command"),
        "policy_category": category,
        "exit_code": result.exit_code,
        "process_status": process_status,
        "sandboxed": data.get("sandboxed"),
    }

    if result.error_category in _BLOCKED_CATEGORIES:
        return _cannot_evaluate(run, check, result, trace, started)

    if category and category != check.kind:
        return _evidence(
            run, check, "ERROR",
            f"The command is a '{category}' command, not a '{check.kind}' check, "
            "so its result does not establish this check.",
            started, observed=observed, traces=[trace],
            next_steps=_steps("ERROR", blocked=True),
        )

    # On failure the gateway falls back to a generic stderr message when the
    # program wrote none; the useful output may be on either stream.
    streams = [
        text.strip()
        for text in (result.stderr, result.stdout)
        if text.strip() and not text.startswith("Command exited with code")
    ]
    body = excerpt("\n".join(streams), 800)

    if result.success:
        return _evidence(
            run, check, "PASS", f"{check.kind} command exited with code 0.", started,
            observed=observed, traces=[trace],
        )

    if result.error_category == "TIMEOUT" or process_status == "TIMEOUT":
        return _evidence(
            run, check, "TIMEOUT", f"{check.kind} command timed out. {body}".strip(), started,
            observed=observed, traces=[trace],
            next_steps=_steps("TIMEOUT", more_output=more),
        )

    missing = _NOT_INSTALLED.search(result.stderr)

    if process_status == "FAIL" and missing:
        # The tool itself is absent: nothing was checked, so this is not a failure of the code.
        return _evidence(
            run, check, "ERROR",
            f"{check.kind} tool is not installed: module '{missing.group(1)}' not found",
            started, observed={**observed, "not_installed": missing.group(1)},
            traces=[trace], next_steps=_steps("ERROR", blocked=True),
        )

    if process_status == "FAIL":
        return _evidence(
            run, check, "FAIL",
            f"{check.kind} command exited with code {result.exit_code}. {body}".strip(),
            started, observed=observed, traces=[trace],
            next_steps=_steps("FAIL", more_output=more),
        )

    return _cannot_evaluate(run, check, result, trace, started)


# --------------------------------------------------------- files and content


async def _check_file_exists(
    run: _Run, check: FileExistsCheck, started: float
) -> CheckEvidence:
    result, trace = await run.call("FILE_INFO", {"path": check.path})

    if not result.success:
        return _cannot_evaluate(run, check, result, trace, started)

    exists = bool(result.data.get("exists"))
    actual_type = result.data.get("type")
    observed = {"path": check.path, "exists": exists, "type": actual_type}

    if check.must_exist:
        ok = exists and (check.type == "any" or actual_type == check.type)
        message = (
            f"{check.path} exists"
            if ok
            else f"{check.path} does not exist"
            if not exists
            else f"{check.path} is a {actual_type}, expected {check.type}"
        )
    else:
        ok = not exists
        message = f"{check.path} is absent" if ok else f"{check.path} exists but must not"

    status: VerificationStatus = "PASS" if ok else "FAIL"
    return _evidence(run, check, status, message, started, observed=observed, traces=[trace])


async def _check_file_content(
    run: _Run, check: FileContentCheck, started: float
) -> CheckEvidence:
    info, info_trace = await run.call("FILE_INFO", {"path": check.path})
    traces = [info_trace]

    if not info.success:
        return _cannot_evaluate(run, check, info, info_trace, started)

    data = info.data
    observed: dict[str, Any] = {"path": check.path}

    if not data.get("exists"):
        return _evidence(
            run, check, "FAIL", f"{check.path} does not exist", started,
            observed={**observed, "exists": False}, traces=traces,
            next_steps=_steps("FAIL"),
        )

    if data.get("type") != "file":
        return _evidence(
            run, check, "FAIL", f"{check.path} is not a regular file", started,
            observed={**observed, "type": data.get("type")}, traces=traces,
            next_steps=_steps("FAIL"),
        )

    if data.get("restricted"):
        return _evidence(
            run, check, "ERROR",
            f"{check.path} is a restricted file and is never read for verification",
            started, observed=observed, traces=traces,
            next_steps=_steps("ERROR", blocked=True),
        )

    actual_sha = data.get("sha256")
    text = ""

    if check.contains or check.not_contains:
        if data.get("binary"):
            return _evidence(
                run, check, "ERROR", f"{check.path} is binary; text conditions do not apply",
                started, observed=observed, traces=traces,
                next_steps=_steps("ERROR", blocked=True),
            )

        read, read_trace = await run.call("READ", {"path": check.path})
        traces.append(read_trace)

        if not read.success:
            return _cannot_evaluate(run, check, read, read_trace, started)

        if read.data.get("truncated"):
            return _evidence(
                run, check, "ERROR",
                f"{check.path} is too large to check completely",
                started, observed=observed, traces=traces,
                next_steps=_steps("ERROR"),
            )

        text = read.stdout.replace("\r\n", "\n")
        actual_sha = read.data.get("sha256", actual_sha)

        if data.get("sha256") and read.data.get("sha256") != data.get("sha256"):
            return _evidence(
                run, check, "ERROR",
                f"{check.path} changed while it was being checked",
                started, observed=observed, traces=traces,
                next_steps=_steps("ERROR"),
            )

    problems: list[str] = []
    observed["sha256"] = actual_sha

    if check.sha256:
        if not actual_sha:
            return _evidence(
                run, check, "ERROR", f"could not hash {check.path}", started,
                observed=observed, traces=traces, next_steps=_steps("ERROR"),
            )

        if actual_sha.lower() != check.sha256.lower():
            problems.append("sha256 differs from the expected value")
            observed["expected_sha256"] = check.sha256.lower()

    missing = [n for n in check.contains if n.replace("\r\n", "\n") not in text]
    present = [n for n in check.not_contains if n.replace("\r\n", "\n") in text]

    if missing:
        problems.append(f"{len(missing)} expected text(s) not found")
        observed["missing"] = [_short(n) for n in missing[:MAX_LISTED]]

    if present:
        problems.append(f"{len(present)} forbidden text(s) found")
        observed["forbidden_present"] = [_short(n) for n in present[:MAX_LISTED]]

    if problems:
        return _evidence(
            run, check, "FAIL", f"{check.path}: " + "; ".join(problems), started,
            observed=observed, traces=traces, next_steps=_steps("FAIL"),
        )

    return _evidence(
        run, check, "PASS", f"{check.path} satisfies all content conditions", started,
        observed=observed, traces=traces,
    )


# ------------------------------------------------------------------- diff


def parse_diff_lines(diff: str) -> tuple[list[str], list[str]]:
    """(added lines, removed lines) of a unified diff, without markers.

    File headers (`--- a/x`, `+++ b/x`) are skipped by position (they come
    before the first `@@` of a file), not by their text, so an added line
    that happens to start with `++` is still counted.
    """

    added: list[str] = []
    removed: list[str] = []
    in_hunk = False

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            in_hunk = False
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk:
            if line.startswith("+"):
                added.append(line[1:])
            elif line.startswith("-"):
                removed.append(line[1:])

    return added, removed


async def _check_diff(run: _Run, check: DiffContainsCheck, started: float) -> CheckEvidence:
    payload: dict[str, Any] = {
        "mode": "base",
        "base": check.base,
        "include_untracked": True,
    }

    if check.paths is not None:
        payload["paths"] = check.paths

    result, trace = await run.call("GIT_DIFF", payload)

    if not result.success:
        return _cannot_evaluate(run, check, result, trace, started)

    added, removed = parse_diff_lines(result.stdout)
    stats = result.data.get("stats") or {}
    files = int(stats.get("files", 0))
    skipped = result.data.get("skipped_untracked") or []
    missing_added = [n for n in check.added_contains if not any(n in line for line in added)]
    missing_removed = [n for n in check.removed_contains if not any(n in line for line in removed)]
    too_few_files = files < check.min_files_changed
    observed: dict[str, Any] = {
        "base": check.base,
        "files_changed": files,
        "additions": stats.get("additions"),
        "deletions": stats.get("deletions"),
        "changed_files": (result.data.get("changed_files") or [])[:MAX_LISTED],
        "diff_truncated": result.truncated,
        "skipped_untracked": skipped[:MAX_LISTED],
    }

    unmet = missing_added or missing_removed or too_few_files

    if not unmet:
        return _evidence(
            run, check, "PASS", "The change set contains the expected changes", started,
            observed=observed, traces=[trace],
        )

    problems = []

    if too_few_files:
        problems.append(f"{files} file(s) changed, expected at least {check.min_files_changed}")

    if missing_added:
        observed["missing_added"] = [_short(n) for n in missing_added[:MAX_LISTED]]
        problems.append(f"{len(missing_added)} expected added line(s) not found")

    if missing_removed:
        observed["missing_removed"] = [_short(n) for n in missing_removed[:MAX_LISTED]]
        problems.append(f"{len(missing_removed)} expected removed line(s) not found")

    if (missing_added or missing_removed) and (result.truncated or skipped):
        # Absence cannot be concluded from an incomplete diff.
        return _evidence(
            run, check, "ERROR",
            "The diff is incomplete (truncated or files skipped), so the missing "
            "lines cannot be ruled out: " + "; ".join(problems),
            started, observed=observed, traces=[trace],
            next_steps=_steps("ERROR"),
        )

    return _evidence(
        run, check, "FAIL", "; ".join(problems), started,
        observed=observed, traces=[trace], next_steps=_steps("FAIL"),
    )


# --------------------------------------------------------- prohibited paths


async def _changed_paths(run: _Run, base: str | None) -> tuple[set[str], list[ToolTrace], str | None]:
    """Union of working-tree changes and (optionally) committed ones."""

    traces: list[ToolTrace] = []
    result, trace = await run.call("GIT_STATUS", {"max_entries": 500})
    traces.append(trace)

    if not result.success:
        return set(), traces, f"git.status failed: {result.stderr.strip() or result.error_category}"

    if result.data.get("truncated"):
        return set(), traces, "too many changed paths to check completely"

    paths: set[str] = set(result.data.get("untracked") or [])

    for key in ("staged", "unstaged", "conflicted"):
        paths.update(result.data.get(key) or [])

    for entry in result.data.get("entries") or []:
        paths.add(entry["path"])

        if entry.get("original_path"):
            paths.add(entry["original_path"])

    if base:
        diff, diff_trace = await run.call(
            "GIT_DIFF",
            {"mode": "base", "base": base, "include_untracked": False, "max_bytes": 1},
        )
        traces.append(diff_trace)

        if not diff.success:
            return set(), traces, f"git.diff failed: {diff.stderr.strip() or diff.error_category}"

        for item in diff.data.get("files") or []:
            paths.add(item["path"])

            if item.get("original_path"):
                paths.add(item["original_path"])

    return paths, traces, None


async def _check_prohibited(
    run: _Run, check: NoProhibitedPathsCheck, started: float
) -> CheckEvidence:
    paths, traces, problem = await _changed_paths(run, check.base)

    if problem:
        return _evidence(
            run, check, "ERROR", problem, started, traces=traces, next_steps=_steps("ERROR")
        )

    # Prohibited matching ignores case (stricter); an allow-list does not
    # (a path outside it is a violation).
    prohibited = GlobSet(check.prohibited, ignore_case=True)
    allowed = GlobSet(check.allowed_only, ignore_case=False) if check.allowed_only is not None else None
    violations = []
    outside = []

    for path in sorted(paths):
        pattern = prohibited.match(path)

        if pattern:
            violations.append({"path": path, "pattern": pattern})
        elif allowed is not None and allowed.match(path) is None:
            outside.append(path)

    observed = {
        "changed_paths": len(paths),
        "violations": violations[:MAX_LISTED],
        "outside_allowed": outside[:MAX_LISTED],
        "base": check.base,
        "note": "git-ignored files are not visible to this check",
    }

    if violations or outside:
        parts = []

        if violations:
            parts.append(f"{len(violations)} prohibited path(s) changed: " + ", ".join(v["path"] for v in violations[:5]))

        if outside:
            parts.append(f"{len(outside)} path(s) changed outside the allowed set: " + ", ".join(outside[:5]))

        return _evidence(
            run, check, "FAIL", "; ".join(parts), started,
            observed=observed, traces=traces,
            next_steps=["inspect", "modify_patch", "replan"],
        )

    return _evidence(
        run, check, "PASS", f"{len(paths)} changed path(s), none prohibited", started,
        observed=observed, traces=traces,
    )


# ------------------------------------------------------------------ driver

_PROCESS_KINDS = frozenset({"tests", "build", "lint", "typecheck"})


async def _fingerprint(workspace: Path) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(workspace_version, workspace)
    except Exception as exc:  # fingerprinting must never break verification
        return {"kind": "unknown", "error": f"{type(exc).__name__}: {exc}"}


def _same_version(a: dict[str, Any], b: dict[str, Any]) -> bool | None:
    fa, fb = a.get("fingerprint"), b.get("fingerprint")

    if not fa or not fb:
        return None

    return fa == fb


async def _dispatch(run: _Run, check: Check) -> CheckEvidence:
    started = time.perf_counter()

    try:
        if isinstance(check, TestsCheck):
            return await _check_tests(run, check, started)

        if isinstance(check, CommandCheck):
            return await _check_command(run, check, started)

        if isinstance(check, FileExistsCheck):
            return await _check_file_exists(run, check, started)

        if isinstance(check, FileContentCheck):
            return await _check_file_content(run, check, started)

        if isinstance(check, DiffContainsCheck):
            return await _check_diff(run, check, started)

        return await _check_prohibited(run, check, started)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # a bug in a check is an ERROR, never a pass
        return _evidence(
            run, check, "ERROR", f"internal error: {type(exc).__name__}: {exc}", started,
            next_steps=_steps("ERROR"),
        )


async def run_checks(
    gateway: ExecutionGateway,
    checks: list[Check],
    *,
    fail_fast: bool = False,
    require_stable: bool = True,
    run_id: str | None = None,
) -> VerificationReport:
    """Run `checks` in order against the gateway's workspace.

    Overall status: TIMEOUT > ERROR > FAIL > PASS; no checks is an ERROR.
    With `require_stable`, a PASS is downgraded to ERROR when the workspace
    fingerprint differs before and after (the result then cannot be tied to
    one revision).
    """

    run = _Run(gateway, run_id or uuid.uuid4().hex[:12])
    started_at = _now()
    started = time.perf_counter()

    if not checks:
        return VerificationReport(
            run_id=run.run_id, status="ERROR", summary="No verification checks were provided.",
            workspace=str(gateway.workspace), started_at=started_at, finished_at=_now(),
        )

    version_before = await _fingerprint(gateway.workspace)
    version_now = version_before
    evidence: list[CheckEvidence] = []
    skipped: list[str] = []

    for index, check in enumerate(checks):
        run.index = index

        if fail_fast and any(e.status != "PASS" for e in evidence):
            skipped.append(describe(check))
            continue

        item = await _dispatch(run, check)

        if check.kind in _PROCESS_KINDS:
            after = await _fingerprint(gateway.workspace)
            item.workspace_version = after
            item.modified_workspace = _same_version(version_now, after) is False
            version_now = after

        evidence.append(item)

    stable = _same_version(version_before, version_now)
    status = fold_status([e.status for e in evidence])
    counts = {s: sum(e.status == s for e in evidence) for s in ("PASS", "FAIL", "ERROR", "TIMEOUT")}
    summary = (
        f"{counts['PASS']} of {len(evidence)} check(s) passed"
        + "".join(f", {counts[s]} {s.lower()}" for s in ("FAIL", "ERROR", "TIMEOUT") if counts[s])
        + (f", {len(skipped)} skipped" if skipped else "")
        + "."
    )

    if status == "PASS" and require_stable and stable is False:
        status = "ERROR"
        summary = (
            "Every check passed, but the workspace changed while verification ran, so "
            "the result cannot be tied to one revision. " + summary
        )

    return VerificationReport(
        run_id=run.run_id,
        status=status,
        summary=summary,
        checks=evidence,
        skipped=skipped,
        workspace=str(gateway.workspace),
        version_before=version_before,
        version_after=version_now,
        stable=stable,
        started_at=started_at,
        finished_at=_now(),
        duration_ms=int((time.perf_counter() - started) * 1000),
    )


async def report_is_current(workspace: Path, report: VerificationReport) -> dict[str, Any]:
    """Does the workspace still match what `report` verified?"""

    now = await _fingerprint(workspace)
    same = _same_version(report.version_after, now)
    return {
        "current": same,
        "verified_fingerprint": report.version_after.get("fingerprint"),
        "workspace_fingerprint": now.get("fingerprint"),
        "reason": (
            "unchanged since verification" if same
            else "workspace differs from the verified version" if same is False
            else "could not compare versions"
        ),
    }
