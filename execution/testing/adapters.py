"""Framework adapters: detect how a workspace is tested, build the command,
validate targets, and know where results come from.

Detection prefers what the repository itself documents (config files,
declared scripts, build files) over guessing, and records its evidence so a
caller can see *why* a framework was chosen. Nothing here runs a program.
"""

import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from execution.testing import console
from execution.testing.model import CaseResult, Counts
from execution.versioning import EXCLUDED_DIRS

Parsed = tuple[Counts, list[CaseResult]]
MAX_TARGETS = 50
MAX_TARGET_CHARS = 300
_SCAN_FILE_LIMIT = 3000
_SCAN_DEPTH = 4


class TargetError(ValueError):
    """A requested test target is not acceptable."""


@dataclass
class Detection:
    framework: str
    evidence: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _read(path: Path, limit: int = 200_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _scan(ws: Path, patterns: tuple[re.Pattern, ...]) -> list[Path]:
    found: list[Path] = []
    seen = 0

    for root, dirs, files in os.walk(ws):
        depth = len(Path(root).relative_to(ws).parts)
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS and depth < _SCAN_DEPTH]

        for name in files:
            seen += 1

            if seen > _SCAN_FILE_LIMIT:
                return found

            if any(p.fullmatch(name) for p in patterns):
                found.append(Path(root) / name)

    return found


_PY_TEST_FILE = (re.compile(r"test_.*\.py"), re.compile(r".*_test\.py"))


def _common_target_checks(targets: list[str]) -> None:
    if len(targets) > MAX_TARGETS:
        raise TargetError(f"At most {MAX_TARGETS} targets are allowed.")

    for target in targets:
        if (
            not isinstance(target, str)
            or not target.strip()
            or len(target) > MAX_TARGET_CHARS
            or "\0" in target
            or "\n" in target
            or "\r" in target
        ):
            raise TargetError(f"Invalid test target: {str(target)[:60]!r}")

        if target.startswith("-"):
            raise TargetError(f"A test target cannot start with '-': {target!r}")


def _existing_inside(ws: Path, value: str, original: str) -> None:
    resolved = (ws / value).resolve()

    if not resolved.is_relative_to(ws):
        raise TargetError(f"Test target leaves the workspace: {original!r}")

    if not resolved.exists():
        raise TargetError(f"Test target does not exist: {original!r}")


class Adapter(ABC):
    name: str = ""
    # junit-file: we ask the runner to write one; junit-dir: the build tool
    # writes reports to known folders; json: stdout is a JSON event stream.
    report_kind: str = "console"

    @abstractmethod
    def detect(self, ws: Path) -> Detection | None: ...

    @abstractmethod
    def command(
        self, ws: Path, targets: list[str], keyword: str | None, report_name: str | None
    ) -> list[str]: ...

    def validate_targets(self, ws: Path, targets: list[str]) -> None:
        _common_target_checks(targets)

    def junit_dirs(self, ws: Path) -> list[Path]:
        return []

    def parse_console(self, text: str) -> Parsed | None:
        return None

    def no_tests_exit_codes(self) -> tuple[int, ...]:
        return ()


class PytestAdapter(Adapter):
    name = "pytest"
    report_kind = "junit-file"

    def detect(self, ws: Path) -> Detection | None:
        evidence: list[str] = []

        for name in ("pytest.ini", ".pytest.ini"):
            if (ws / name).is_file():
                evidence.append(f"{name} present")

        for name, marker in (
            ("pyproject.toml", "[tool.pytest"),
            ("tox.ini", "[pytest]"),
            ("setup.cfg", "[tool:pytest]"),
        ):
            if marker in _read(ws / name):
                evidence.append(f"{marker} configured in {name}")

        if (ws / "conftest.py").is_file() or (ws / "tests" / "conftest.py").is_file():
            evidence.append("conftest.py present")

        test_files = _scan(ws, _PY_TEST_FILE)
        imports_pytest = [
            f for f in test_files[:50] if re.search(r"^\s*(import|from)\s+pytest\b", _read(f, 8000), re.M)
        ]

        if imports_pytest:
            evidence.append(f"{len(imports_pytest)} test file(s) import pytest")

        for name in ("requirements.txt", "requirements-dev.txt", "pyproject.toml"):
            if re.search(r"(?im)^\s*[\"']?pytest\b", _read(ws / name)):
                evidence.append(f"pytest listed in {name}")
                break

        if evidence:
            return Detection("pytest", evidence)

        unittest_style = [
            f for f in test_files[:50] if "unittest" in _read(f, 8000)
        ]

        if test_files and not unittest_style:
            return Detection(
                "pytest",
                [f"{len(test_files)} test_*.py file(s) found"],
                ["no pytest configuration found; assuming pytest conventions"],
            )

        return None

    def validate_targets(self, ws: Path, targets: list[str]) -> None:
        _common_target_checks(targets)

        for target in targets:
            _existing_inside(ws, target.split("::", 1)[0], target)

    def command(self, ws, targets, keyword, report_name):
        cmd = ["python", "-m", "pytest", "-q"]

        if report_name:
            cmd.append(f"--junitxml={report_name}")

        if keyword:
            cmd += ["-k", keyword]

        return [*cmd, *targets]

    def parse_console(self, text):
        return console.parse_pytest(text)

    def no_tests_exit_codes(self):
        return (5,)


class UnittestAdapter(Adapter):
    name = "unittest"
    report_kind = "console"

    def detect(self, ws: Path) -> Detection | None:
        files = _scan(ws, _PY_TEST_FILE + (re.compile(r"test.*\.py"),))
        users = [f for f in files[:50] if "unittest" in _read(f, 8000)]

        if users:
            return Detection("unittest", [f"{len(users)} test file(s) use unittest"])

        return None

    def validate_targets(self, ws: Path, targets: list[str]) -> None:
        _common_target_checks(targets)

        for target in targets:
            if not re.fullmatch(r"[A-Za-z_][\w]*(\.[A-Za-z_][\w]*)*", target):
                raise TargetError(
                    f"unittest targets must be dotted names (pkg.module.Class.test): {target!r}"
                )

    def command(self, ws, targets, keyword, report_name):
        if targets:
            return ["python", "-m", "unittest", "-v", *targets]

        return ["python", "-m", "unittest", "discover", "-v"]

    def parse_console(self, text):
        return console.parse_unittest(text)

    def no_tests_exit_codes(self):
        return (5,)


class NpmAdapter(Adapter):
    name = "npm"
    report_kind = "console"

    def detect(self, ws: Path) -> Detection | None:
        package = ws / "package.json"

        if not package.is_file():
            return None

        try:
            data = json.loads(_read(package))
        except json.JSONDecodeError:
            return Detection("npm", ["package.json present but unreadable"], ["package.json is not valid JSON"])

        script = (data.get("scripts") or {}).get("test")

        if not script or "no test specified" in script:
            return None

        runner = next((r for r in ("vitest", "jest", "mocha") if r in script), None)
        evidence = [f'package.json scripts.test = "{script[:80]}"']

        if runner:
            evidence.append(f"runner looks like {runner}")

        return Detection("npm", evidence)

    def validate_targets(self, ws: Path, targets: list[str]) -> None:
        _common_target_checks(targets)

        for target in targets:
            _existing_inside(ws, target, target)

    def command(self, ws, targets, keyword, report_name):
        return ["npm", "test", *(["--", *targets] if targets else [])]

    def parse_console(self, text):
        return console.parse_node(text)


class MavenAdapter(Adapter):
    name = "maven"
    report_kind = "junit-dir"

    def detect(self, ws: Path) -> Detection | None:
        return Detection("maven", ["pom.xml present"]) if (ws / "pom.xml").is_file() else None

    def validate_targets(self, ws: Path, targets: list[str]) -> None:
        _common_target_checks(targets)

        for target in targets:
            if not re.fullmatch(r"[\w.*$]+(#[\w*$]+)?", target):
                raise TargetError(f"Maven targets look like Class or Class#method: {target!r}")

    def command(self, ws, targets, keyword, report_name):
        cmd = ["mvn", "-B", "test"]

        if targets:
            cmd += [f"-Dtest={','.join(targets)}", "-Dsurefire.failIfNoSpecifiedTests=false"]

        return cmd

    def junit_dirs(self, ws: Path) -> list[Path]:
        return [p for p in ws.glob("**/target/surefire-reports") if p.is_dir()][:20]


class GradleAdapter(Adapter):
    name = "gradle"
    report_kind = "junit-dir"

    def detect(self, ws: Path) -> Detection | None:
        for name in ("build.gradle", "build.gradle.kts"):
            if (ws / name).is_file():
                return Detection(
                    "gradle",
                    [f"{name} present"],
                    ["the Gradle wrapper (./gradlew) is not used; `gradle` must be on PATH"],
                )

        return None

    def validate_targets(self, ws: Path, targets: list[str]) -> None:
        _common_target_checks(targets)

        for target in targets:
            if not re.fullmatch(r"[\w.*$#]+", target):
                raise TargetError(f"Gradle --tests patterns look like pkg.Class.method: {target!r}")

    def command(self, ws, targets, keyword, report_name):
        cmd = ["gradle", "test", "--console=plain"]

        for target in targets:
            cmd += ["--tests", target]

        return cmd

    def junit_dirs(self, ws: Path) -> list[Path]:
        return [p for p in ws.glob("**/build/test-results/test") if p.is_dir()][:20]


class CargoAdapter(Adapter):
    name = "cargo"
    report_kind = "console"

    def detect(self, ws: Path) -> Detection | None:
        return Detection("cargo", ["Cargo.toml present"]) if (ws / "Cargo.toml").is_file() else None

    def validate_targets(self, ws: Path, targets: list[str]) -> None:
        _common_target_checks(targets)

        for target in targets:
            if not re.fullmatch(r"[\w:]+", target):
                raise TargetError(f"cargo test filters are test names like module::test: {target!r}")

    def command(self, ws, targets, keyword, report_name):
        return ["cargo", "test", *targets]

    def parse_console(self, text):
        return console.parse_cargo(text)


class GoAdapter(Adapter):
    name = "go"
    report_kind = "json"

    def detect(self, ws: Path) -> Detection | None:
        return Detection("go", ["go.mod present"]) if (ws / "go.mod").is_file() else None

    def validate_targets(self, ws: Path, targets: list[str]) -> None:
        _common_target_checks(targets)

        for target in targets:
            if not re.fullmatch(r"(\./)?[\w./\-]*(\.\.\.)?", target):
                raise TargetError(f"go test targets are package paths like ./pkg or ./...: {target!r}")

            base = target.removesuffix("...").rstrip("/")

            if base not in ("", ".", "./"):
                _existing_inside(ws, base, target)

    def command(self, ws, targets, keyword, report_name):
        return ["go", "test", "-json", *(targets or ["./..."])]


ADAPTERS: dict[str, Adapter] = {
    a.name: a
    for a in (
        PytestAdapter(),
        UnittestAdapter(),
        MavenAdapter(),
        GradleAdapter(),
        NpmAdapter(),
        CargoAdapter(),
        GoAdapter(),
    )
}
AUTO_ORDER = ["pytest", "unittest", "maven", "gradle", "npm", "cargo", "go"]


def detect_all(ws: Path) -> list[Detection]:
    found = []

    for name in AUTO_ORDER:
        detection = ADAPTERS[name].detect(ws)

        if detection is not None:
            found.append(detection)

    return found
