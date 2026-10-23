"""JUnit XML reports (pytest, Maven Surefire, Gradle, Jest-JUnit, ...).

Counts are derived from the individual <testcase> elements; the totals the
file claims in its attributes are only used to detect a report that does not
agree with itself. DOCTYPE/ENTITY declarations are refused outright (no
entity-expansion attacks from a malicious test run), and files are size
bounded.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from execution.testing.model import CaseResult, Counts

MAX_REPORT_BYTES = 20 * 1024 * 1024
MAX_MESSAGE_CHARS = 500


@dataclass
class JUnitParse:
    cases: list[CaseResult] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return bool(self.files_read) and not any(
            p.startswith("corrupt") for p in self.problems
        )

    def counts(self) -> Counts:
        by = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}

        for case in self.cases:
            by[case.status] += 1

        return Counts(
            total=len(self.cases),
            passed=by["passed"],
            failed=by["failed"],
            errors=by["error"],
            skipped=by["skipped"],
        )


def _text(element: ET.Element | None) -> str:
    if element is None:
        return ""

    message = element.get("message") or (element.text or "").strip()
    return message[:MAX_MESSAGE_CHARS]


def _case(element: ET.Element) -> CaseResult:
    status = "passed"
    message = ""

    for tag, mapped in (("error", "error"), ("failure", "failed"), ("skipped", "skipped")):
        child = element.find(tag)

        if child is not None:
            status, message = mapped, _text(child)
            break

    duration = element.get("time")

    try:
        seconds = float(duration) if duration is not None else None
    except ValueError:
        seconds = None

    return CaseResult(
        name=element.get("name", ""),
        classname=element.get("classname", ""),
        file=element.get("file", ""),
        status=status,  # type: ignore[arg-type]
        duration_s=seconds,
        message=message,
    )


def _declared_total(root: ET.Element) -> int | None:
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))

    if root.tag == "testsuites" and root.get("tests") is not None:
        try:
            return int(root.get("tests"))
        except ValueError:
            return None

    # Nested suites repeat their children's totals; count leaf suites only.
    leaves = [s for s in suites if s.find("testsuite") is None]

    try:
        return sum(int(s.get("tests", "0")) for s in leaves)
    except ValueError:
        return None


def parse_junit(paths: list[Path]) -> JUnitParse:
    result = JUnitParse()

    for path in paths:
        try:
            raw = path.read_bytes()
        except OSError as exc:
            result.problems.append(f"corrupt: cannot read {path.name}: {exc}")
            continue

        if len(raw) > MAX_REPORT_BYTES:
            result.problems.append(f"corrupt: {path.name} is larger than {MAX_REPORT_BYTES} bytes")
            continue

        head = raw[:4096].lower()

        if b"<!doctype" in raw[:65536].lower() or b"<!entity" in raw[:65536].lower() or b"<!doctype" in head:
            result.problems.append(f"corrupt: {path.name} contains a DOCTYPE/ENTITY declaration")
            continue

        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            result.problems.append(f"corrupt: {path.name} is not valid XML ({exc})")
            continue

        if root.tag not in ("testsuites", "testsuite"):
            result.problems.append(f"corrupt: {path.name} is not a JUnit report (root <{root.tag}>)")
            continue

        cases = [_case(e) for e in root.iter("testcase")]
        declared = _declared_total(root)

        if declared is not None and declared != len(cases):
            result.problems.append(
                f"inconsistent: {path.name} declares {declared} tests but contains {len(cases)}"
            )

        result.cases.extend(cases)
        result.files_read.append(path.name)

    return result
