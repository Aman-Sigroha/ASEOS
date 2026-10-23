"""Best-effort parsers for framework summaries, used when no structured
report exists (or as a cross-check). Each returns `(Counts, cases)` or None
when the text contains no recognizable summary.
"""

import json
import re

from execution.testing.model import CaseResult, Counts

Parsed = tuple[Counts, list[CaseResult]]

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def clean(text: str) -> str:
    return _ANSI.sub("", text)


# ---------------------------------------------------------------- pytest

_PYTEST_ITEM = re.compile(
    r"(\d+) (failed|passed|skipped|deselected|xfailed|xpassed|errors?|warnings?)"
)


def parse_pytest(text: str) -> Parsed | None:
    for line in reversed(clean(text).splitlines()):
        line = line.strip(" =")

        if not re.search(r" in [0-9.]+s", line) and not re.search(r"no tests ran", line):
            continue

        found = {k: int(v) for v, k in _PYTEST_ITEM.findall(line)}

        if not found and "no tests ran" not in line:
            continue

        passed = found.get("passed", 0) + found.get("xpassed", 0)
        failed = found.get("failed", 0)
        errors = found.get("error", 0) + found.get("errors", 0)
        skipped = found.get("skipped", 0) + found.get("xfailed", 0)
        counts = Counts(
            total=passed + failed + errors + skipped,
            passed=passed,
            failed=failed,
            errors=errors,
            skipped=skipped,
            deselected=found.get("deselected"),
        )
        return counts, []

    return None


# -------------------------------------------------------------- unittest

_UNITTEST_RAN = re.compile(r"^Ran (\d+) tests? in ", re.M)
_UNITTEST_FAIL = re.compile(r"^FAILED \((.*)\)\s*$", re.M)
_UNITTEST_OK = re.compile(r"^OK(?: \((.*)\))?\s*$", re.M)
_UNITTEST_CASE = re.compile(
    r"^(?P<name>\S+) \((?P<cls>[\w.]+)\)(?:.*?) \.\.\. "
    r"(?P<res>ok|FAIL|ERROR|skipped.*|expected failure|unexpected success)\s*$",
    re.M,
)


def _kv(text: str | None) -> dict[str, int]:
    return {k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", text or "")}


def parse_unittest(text: str) -> Parsed | None:
    text = clean(text)
    ran = _UNITTEST_RAN.search(text)

    if not ran:
        return None

    total = int(ran.group(1))
    failure = _UNITTEST_FAIL.search(text)
    ok = _UNITTEST_OK.search(text)
    stats = _kv(failure.group(1) if failure else (ok.group(1) if ok else ""))

    if not failure and not ok:
        return None

    failed = stats.get("failures", 0)
    errors = stats.get("errors", 0)
    skipped = stats.get("skipped", 0)
    passed = max(total - failed - errors - skipped, 0)
    cases = []

    for match in _UNITTEST_CASE.finditer(text):
        res = match.group("res")
        status = (
            "passed" if res in ("ok", "expected failure")
            else "skipped" if res.startswith("skipped")
            else "failed" if res in ("FAIL", "unexpected success")
            else "error"
        )
        name, dotted = match.group("name"), match.group("cls")
        classname = dotted.rsplit(".", 1)[0] if dotted.endswith("." + name) else dotted
        cases.append(CaseResult(name=name, classname=classname, status=status))  # type: ignore[arg-type]

    return Counts(total=total, passed=passed, failed=failed, errors=errors, skipped=skipped), cases


# ------------------------------------------------- jest / vitest / mocha

_JEST = re.compile(r"^Tests:\s+(.*?)(?:, (\d+) total)?\s*$", re.M)
_VITEST = re.compile(r"^\s*Tests\s+(.*?)\s*\((\d+)\)\s*$", re.M)


def _words(text: str) -> dict[str, int]:
    return {k: int(v) for v, k in re.findall(r"(\d+) (failed|passed|skipped|todo|pending)", text)}


_TAP_SUMMARY = re.compile(r"^# (tests|pass|fail|cancelled|skipped|todo) (\d+)\s*$", re.M)
_TAP_CASE = re.compile(r"^(not ok|ok) \d+ - (.*?)(?: # (SKIP|TODO)\b.*)?$", re.M)


def _parse_tap(text: str) -> Parsed | None:
    """TAP as printed by `node --test` (top-level results and its `# pass N`
    summary). Both must be present: a lone `ok` line is not a summary."""

    found = {key: int(value) for key, value in _TAP_SUMMARY.findall(text)}

    if "tests" not in found or "pass" not in found or "fail" not in found:
        return None

    cases = [
        CaseResult(
            name=name.strip(),
            status="skipped" if directive else ("failed" if verdict == "not ok" else "passed"),
        )
        for verdict, name, directive in _TAP_CASE.findall(text)
    ]
    skipped = found.get("skipped", 0) + found.get("todo", 0)
    errors = found.get("cancelled", 0)

    return Counts(
        total=found["tests"], passed=found["pass"], failed=found["fail"],
        errors=errors, skipped=skipped,
    ), cases


def parse_node(text: str) -> Parsed | None:
    text = clean(text)

    for pattern in (_JEST, _VITEST):
        match = pattern.search(text)

        if match:
            found = _words(match.group(1))
            passed, failed = found.get("passed", 0), found.get("failed", 0)
            skipped = found.get("skipped", 0) + found.get("todo", 0) + found.get("pending", 0)
            return Counts(
                total=passed + failed + skipped, passed=passed, failed=failed, skipped=skipped
            ), []

    tap = _parse_tap(text)

    if tap is not None:
        return tap

    passing = re.search(r"^\s*(\d+) passing", text, re.M)

    if passing:
        failing = re.search(r"^\s*(\d+) failing", text, re.M)
        pending = re.search(r"^\s*(\d+) pending", text, re.M)
        p, f = int(passing.group(1)), int(failing.group(1)) if failing else 0
        s = int(pending.group(1)) if pending else 0
        return Counts(total=p + f + s, passed=p, failed=f, skipped=s), []

    return None


# ------------------------------------------------------------------ cargo

_CARGO = re.compile(
    r"test result: (?:ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored"
    r"(?:; \d+ measured)?(?:; (\d+) filtered out)?"
)


def parse_cargo(text: str) -> Parsed | None:
    matches = _CARGO.findall(clean(text))

    if not matches:
        return None

    passed = sum(int(m[0]) for m in matches)
    failed = sum(int(m[1]) for m in matches)
    skipped = sum(int(m[2]) for m in matches)
    deselected = sum(int(m[3] or 0) for m in matches)
    return Counts(
        total=passed + failed + skipped, passed=passed, failed=failed,
        skipped=skipped, deselected=deselected,
    ), []


# --------------------------------------------------------------- go -json


def parse_go_json(text: str) -> tuple[Parsed | None, list[str]]:
    """`go test -json` event stream. Returns (parsed, problems)."""

    cases: list[CaseResult] = []
    problems: list[str] = []
    seen_event = False
    package_failures = 0

    for line in text.splitlines():
        line = line.strip()

        if not line.startswith("{"):
            continue

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            problems.append("corrupt: a line of go test -json output is not JSON")
            continue

        seen_event = True
        action, test = event.get("Action"), event.get("Test")

        if action in ("pass", "fail", "skip") and test:
            cases.append(
                CaseResult(
                    name=test,
                    classname=event.get("Package", ""),
                    status={"pass": "passed", "fail": "failed", "skip": "skipped"}[action],
                    duration_s=event.get("Elapsed"),
                )
            )
        elif action == "fail" and not test:
            package_failures += 1

    if not seen_event:
        return None, problems

    counts = Counts(
        total=len(cases),
        passed=sum(c.status == "passed" for c in cases),
        failed=sum(c.status == "failed" for c in cases),
        skipped=sum(c.status == "skipped" for c in cases),
    )
    failing_tests = counts.failed

    if package_failures and not failing_tests:
        counts.errors = package_failures  # build/setup failure, not a test failure

    return (counts, cases), problems
