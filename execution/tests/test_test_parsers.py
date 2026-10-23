import json
import os
import time

import pytest

from execution.commands.policy import CommandPolicy
from execution.testing import console
from execution.testing.adapters import (
    ADAPTERS,
    TargetError,
    detect_all,
)
from execution.testing.junit import MAX_REPORT_BYTES, parse_junit
from execution.versioning import is_excluded, workspace_version

PYTEST_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites name="pytest tests">
  <testsuite name="pytest" errors="1" failures="1" skipped="1" tests="4" time="0.1">
    <testcase classname="test_a" name="test_ok" time="0.001"/>
    <testcase classname="test_a" name="test_bad" file="test_a.py" time="0.002">
      <failure message="assert 0 == 1">trace</failure>
    </testcase>
    <testcase classname="test_a" name="test_skip"><skipped type="pytest.skip" message="why"/></testcase>
    <testcase classname="test_b" name="test_err"><error message="ImportError: nope">tb</error></testcase>
  </testsuite>
</testsuites>
"""

SUREFIRE_XML = """<?xml version="1.0"?>
<testsuite name="com.x.FooTest" tests="2" errors="0" skipped="0" failures="1">
  <testcase name="a" classname="com.x.FooTest" time="0.01"/>
  <testcase name="b" classname="com.x.FooTest" time="0.02"><failure message="expected 1">x</failure></testcase>
</testsuite>
"""


def write_xml(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ----------------------------------------------------------------- JUnit


def test_junit_classifies_every_case(tmp_path):
    parsed = parse_junit([write_xml(tmp_path, "r.xml", PYTEST_XML)])

    counts = parsed.counts()
    assert parsed.usable and parsed.problems == []
    assert (counts.total, counts.passed, counts.failed, counts.errors, counts.skipped) == (4, 1, 1, 1, 1)
    bad = next(c for c in parsed.cases if c.name == "test_bad")
    assert bad.status == "failed" and bad.message == "assert 0 == 1" and bad.file == "test_a.py"
    assert next(c for c in parsed.cases if c.name == "test_skip").message == "why"


def test_junit_merges_several_files(tmp_path):
    a = write_xml(tmp_path, "a.xml", SUREFIRE_XML)
    b = write_xml(tmp_path, "b.xml", SUREFIRE_XML.replace("FooTest", "BarTest"))

    counts = parse_junit([a, b]).counts()

    assert (counts.total, counts.passed, counts.failed) == (4, 2, 2)


def test_junit_counts_come_from_cases_not_from_claimed_totals(tmp_path):
    lying = PYTEST_XML.replace('tests="4"', 'tests="99"').replace('failures="1"', 'failures="0"')

    parsed = parse_junit([write_xml(tmp_path, "r.xml", lying)])

    assert parsed.counts().failed == 1
    assert any(p.startswith("inconsistent") for p in parsed.problems)
    assert parsed.usable


def test_junit_nested_suites_are_not_double_counted(tmp_path):
    nested = (
        '<testsuites tests="2"><testsuite name="outer" tests="2">'
        '<testsuite name="inner" tests="2"><testcase name="a" classname="x"/>'
        '<testcase name="b" classname="x"/></testsuite></testsuite></testsuites>'
    )

    parsed = parse_junit([write_xml(tmp_path, "n.xml", nested)])

    assert parsed.counts().total == 2 and parsed.problems == []


@pytest.mark.parametrize(
    "content, fragment",
    [
        ("<testsuite><testcase", "not valid XML"),
        ("", "not valid XML"),
        ("plain text, not xml", "not valid XML"),
        ("<html><body/></html>", "not a JUnit report"),
        (
            '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><testsuite>&a;</testsuite>',
            "DOCTYPE",
        ),
        ('<!DOCTYPE testsuite SYSTEM "file:///etc/passwd"><testsuite/>', "DOCTYPE"),
    ],
)
def test_corrupt_or_hostile_reports_are_rejected(tmp_path, content, fragment):
    parsed = parse_junit([write_xml(tmp_path, "bad.xml", content)])

    assert not parsed.usable
    assert fragment in parsed.problems[0] and parsed.problems[0].startswith("corrupt")
    assert parsed.cases == []


def test_entity_expansion_bomb_is_never_expanded(tmp_path):
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
        "]><testsuite><testcase name=\"&lol2;\"/></testsuite>"
    )
    started = time.monotonic()

    parsed = parse_junit([write_xml(tmp_path, "bomb.xml", bomb)])

    assert not parsed.usable and time.monotonic() - started < 2


def test_oversized_and_missing_reports(tmp_path, monkeypatch):
    monkeypatch.setattr("execution.testing.junit.MAX_REPORT_BYTES", 50)
    big = write_xml(tmp_path, "big.xml", PYTEST_XML)

    parsed = parse_junit([big, tmp_path / "missing.xml"])

    assert not parsed.usable
    assert any("larger than" in p for p in parsed.problems)
    assert any("cannot read" in p for p in parsed.problems)
    assert MAX_REPORT_BYTES == 20 * 1024 * 1024


def test_one_good_file_survives_a_corrupt_neighbour(tmp_path):
    good = write_xml(tmp_path, "good.xml", SUREFIRE_XML)
    bad = write_xml(tmp_path, "bad.xml", "<testsuite")

    parsed = parse_junit([good, bad])

    assert parsed.counts().total == 2
    assert parsed.files_read == ["good.xml"]
    assert any(p.startswith("corrupt") for p in parsed.problems)


# --------------------------------------------------------------- console


@pytest.mark.parametrize(
    "line, expected",
    [
        ("=== 2 failed, 3 passed, 1 skipped in 0.12s ===", (6, 3, 2, 0, 1, None)),
        ("1 failed, 2 passed in 0.50s", (3, 2, 1, 0, 0, None)),
        ("5 passed in 0.05s", (5, 5, 0, 0, 0, None)),
        ("3 passed, 2 deselected in 0.02s", (3, 3, 0, 0, 0, 2)),
        ("1 passed, 1 error in 0.01s", (2, 1, 0, 1, 0, None)),
        ("\x1b[31m1 failed\x1b[0m, \x1b[32m1 passed\x1b[0m in 0.01s", (2, 1, 1, 0, 0, None)),
        ("1 passed, 2 xfailed, 1 xpassed, 3 warnings in 1.00s", (4, 2, 0, 0, 2, None)),
    ],
)
def test_pytest_summary_line(line, expected):
    counts, _ = console.parse_pytest("noise\n" + line + "\n")

    assert (
        counts.total, counts.passed, counts.failed, counts.errors,
        counts.skipped, counts.deselected,
    ) == expected


def test_pytest_without_a_summary_is_unrecognized():
    assert console.parse_pytest("collected 0 items\nsomething else") is None
    assert console.parse_pytest("") is None


def test_pytest_no_tests_ran():
    counts, _ = console.parse_pytest("no tests ran in 0.01s")

    assert counts.total == 0


UNITTEST_OK = "test_a (t.A.test_a) ... ok\ntest_b (t.A.test_b) ... skipped 'later'\n\nRan 2 tests in 0.001s\n\nOK (skipped=1)\n"
UNITTEST_BAD = (
    "test_a (t.A.test_a) ... ok\ntest_b (t.A.test_b) ... FAIL\ntest_c (t.A.test_c) ... ERROR\n"
    "\nRan 3 tests in 0.002s\n\nFAILED (failures=1, errors=1)\n"
)


def test_unittest_ok_with_skips():
    counts, cases = console.parse_unittest(UNITTEST_OK)

    assert (counts.total, counts.passed, counts.skipped, counts.failed) == (2, 1, 1, 0)
    assert [c.status for c in cases] == ["passed", "skipped"]


def test_unittest_failures_and_errors():
    counts, cases = console.parse_unittest(UNITTEST_BAD)

    assert (counts.total, counts.passed, counts.failed, counts.errors) == (3, 1, 1, 1)
    assert [c.status for c in cases] == ["passed", "failed", "error"]
    assert cases[1].classname == "t.A"


def test_unittest_without_summary_is_unrecognized():
    assert console.parse_unittest("Ran 3 tests in 0.1s\n") is None
    assert console.parse_unittest("garbage") is None


def test_jest_vitest_mocha():
    jest, _ = console.parse_node("Tests:       1 failed, 2 passed, 3 total\n")
    vitest, _ = console.parse_node(" Tests  1 failed | 4 passed (5)\n")
    mocha, _ = console.parse_node("  3 passing (10ms)\n  1 failing\n  2 pending\n")

    assert (jest.total, jest.passed, jest.failed) == (3, 2, 1)
    assert (vitest.total, vitest.passed, vitest.failed) == (5, 4, 1)
    assert (mocha.total, mocha.passed, mocha.failed, mocha.skipped) == (6, 3, 1, 2)
    assert console.parse_node("nothing here") is None


def test_cargo_sums_every_result_line():
    text = (
        "test result: ok. 3 passed; 0 failed; 1 ignored; 0 measured; 2 filtered out; finished in 0.00s\n"
        "test result: FAILED. 1 passed; 2 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s\n"
    )

    counts, _ = console.parse_cargo(text)

    assert (counts.passed, counts.failed, counts.skipped, counts.deselected) == (4, 2, 1, 2)
    assert console.parse_cargo("no results") is None


GO_JSON = "\n".join(
    json.dumps(e)
    for e in [
        {"Action": "run", "Package": "p", "Test": "TestA"},
        {"Action": "pass", "Package": "p", "Test": "TestA", "Elapsed": 0.1},
        {"Action": "fail", "Package": "p", "Test": "TestB", "Elapsed": 0.2},
        {"Action": "skip", "Package": "p", "Test": "TestC"},
        {"Action": "fail", "Package": "p"},
    ]
)


def test_go_json_events():
    parsed, problems = console.parse_go_json(GO_JSON)

    counts, cases = parsed
    assert problems == []
    assert (counts.total, counts.passed, counts.failed, counts.skipped, counts.errors) == (3, 1, 1, 1, 0)
    assert {c.name: c.status for c in cases} == {"TestA": "passed", "TestB": "failed", "TestC": "skipped"}


def test_go_build_failure_is_an_error_not_a_failed_test():
    text = json.dumps({"Action": "fail", "Package": "p"})

    (counts, _), _ = console.parse_go_json(text)

    assert (counts.total, counts.errors) == (0, 1)


def test_go_json_with_a_corrupt_line_reports_it():
    parsed, problems = console.parse_go_json(GO_JSON + '\n{"Action": "pa')

    assert parsed is not None
    assert any("not JSON" in p for p in problems)
    assert console.parse_go_json("plain text")[0] is None


# ------------------------------------------------------------- detection


def names(ws):
    return [d.framework for d in detect_all(ws)]


def test_empty_workspace_has_no_framework(tmp_path):
    assert names(tmp_path) == []


def test_pytest_is_detected_from_its_own_configuration(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")

    detection = ADAPTERS["pytest"].detect(tmp_path)

    assert detection.framework == "pytest"
    assert "pytest.ini present" in detection.evidence


@pytest.mark.parametrize(
    "name, content, evidence",
    [
        ("pyproject.toml", "[tool.pytest.ini_options]\nx=1\n", "[tool.pytest"),
        ("tox.ini", "[pytest]\n", "[pytest]"),
        ("setup.cfg", "[tool:pytest]\n", "[tool:pytest]"),
        ("requirements-dev.txt", "pytest>=8\n", "pytest listed"),
    ],
)
def test_pytest_detection_evidence(tmp_path, name, content, evidence):
    (tmp_path / name).write_text(content)

    assert any(evidence in e for e in ADAPTERS["pytest"].detect(tmp_path).evidence)


def test_pytest_detected_from_test_files_that_import_it(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("import pytest\ndef test_x(): pass\n")

    detection = ADAPTERS["pytest"].detect(tmp_path)

    assert "import pytest" in detection.evidence[0]


def test_plain_test_files_default_to_pytest_with_a_note(tmp_path):
    (tmp_path / "test_x.py").write_text("def test_x(): pass\n")

    detection = ADAPTERS["pytest"].detect(tmp_path)

    assert detection.notes and "assuming pytest" in detection.notes[0]


def test_unittest_style_tests_are_unittest_not_pytest(tmp_path):
    (tmp_path / "test_x.py").write_text("import unittest\nclass T(unittest.TestCase):\n    def test_x(self): pass\n")

    assert names(tmp_path) == ["unittest"]


def test_npm_needs_a_real_test_script(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": 'echo "Error: no test specified" && exit 1'}}))
    assert names(tmp_path) == []

    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "vitest run"}}))
    detection = ADAPTERS["npm"].detect(tmp_path)

    assert detection.framework == "npm"
    assert any("vitest" in e for e in detection.evidence)


def test_broken_package_json_is_flagged_not_crashed(tmp_path):
    (tmp_path / "package.json").write_text("{nope")

    detection = ADAPTERS["npm"].detect(tmp_path)

    assert "not valid JSON" in detection.notes[0]


@pytest.mark.parametrize(
    "file, framework",
    [
        ("pom.xml", "maven"),
        ("build.gradle", "gradle"),
        ("build.gradle.kts", "gradle"),
        ("Cargo.toml", "cargo"),
        ("go.mod", "go"),
    ],
)
def test_build_file_detection(tmp_path, file, framework):
    (tmp_path / file).write_text("x")

    assert names(tmp_path) == [framework]


def test_several_frameworks_are_all_reported_in_a_stable_order(tmp_path):
    for name in ("go.mod", "pom.xml", "pytest.ini"):
        (tmp_path / name).write_text("[pytest]\n")

    assert names(tmp_path) == ["pytest", "maven", "go"]


def test_vendored_directories_are_not_scanned(tmp_path):
    vendored = tmp_path / "node_modules" / "pkg"
    vendored.mkdir(parents=True)
    (vendored / "test_x.py").write_text("def test_x(): pass\n")

    assert names(tmp_path) == []


# --------------------------------------------- commands and the policy


@pytest.mark.parametrize(
    "framework, files, targets",
    [
        ("pytest", {"tests/test_a.py": ""}, []),
        ("pytest", {"tests/test_a.py": ""}, ["tests/test_a.py::test_x", "tests/test_a.py"]),
        ("unittest", {}, []),
        ("unittest", {}, ["pkg.test_mod.Case.test_one"]),
        ("npm", {"src/a.test.js": ""}, []),
        ("npm", {"src/a.test.js": ""}, ["src/a.test.js"]),
        ("maven", {}, []),
        ("maven", {}, ["FooTest", "BarTest#method", "com.x.*Test"]),
        ("gradle", {}, []),
        ("gradle", {}, ["com.x.FooTest.method", "*Test"]),
        ("cargo", {}, []),
        ("cargo", {}, ["module::test_one"]),
        ("go", {"pkg/a_test.go": ""}, []),
        ("go", {"pkg/a_test.go": ""}, ["./pkg", "./..."]),
    ],
)
def test_every_generated_command_passes_the_default_policy(tmp_path, framework, files, targets):
    for rel, content in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(content)

    adapter = ADAPTERS[framework]
    adapter.validate_targets(tmp_path, targets)
    command = adapter.command(tmp_path, targets, None, ".aseos_report_x.xml" if adapter.report_kind == "junit-file" else None)

    decision = CommandPolicy.default().evaluate(command, tmp_path, tmp_path, frozenset())

    assert decision.allowed, (command, decision.reason)
    assert decision.category == "test"


def test_pytest_keyword_and_report_flag(tmp_path):
    command = ADAPTERS["pytest"].command(tmp_path, [], "slow and not db", ".aseos_report_x.xml")

    assert command == ["python", "-m", "pytest", "-q", "--junitxml=.aseos_report_x.xml", "-k", "slow and not db"]


@pytest.mark.parametrize(
    "framework, target",
    [
        ("pytest", "--collect-only"),
        ("pytest", "-p"),
        ("pytest", "missing_test.py"),
        ("pytest", "../outside.py"),
        ("pytest", "tests/a.py\nb"),
        ("pytest", ""),
        ("pytest", "x" * 400),
        ("unittest", "tests/test_a.py"),
        ("unittest", "a b"),
        ("unittest", "--failfast"),
        ("maven", "Foo;rm -rf"),
        ("maven", "-Dexec.mainClass=X"),
        ("gradle", "x y"),
        ("gradle", "--init-script"),
        ("cargo", "a b"),
        ("cargo", "--config"),
        ("go", "./does-not-exist"),
        ("go", "../elsewhere"),
        ("go", "pkg; rm"),
        ("npm", "../outside.js"),
        ("npm", "nope.js"),
    ],
)
def test_bad_targets_are_rejected_before_anything_runs(tmp_path, framework, target):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "a.py").write_text("")

    with pytest.raises(TargetError):
        ADAPTERS[framework].validate_targets(tmp_path, [target])


def test_too_many_targets(tmp_path):
    with pytest.raises(TargetError):
        ADAPTERS["maven"].validate_targets(tmp_path, ["Foo"] * 51)


# ----------------------------------------------------- go/cargo policy


@pytest.mark.parametrize(
    "argv",
    [
        ["go", "test", "-exec", "evil", "./..."],
        ["go", "test", "-exec=evil"],
        ["go", "test", "-toolexec", "evil"],
        ["go", "test", "-overlay=x.json"],
        ["cargo", "test", "--config", "target.x.runner='evil'"],
        ["cargo", "test", "-Zunstable"],
        ["gradle", "test", "--init-script", "x.gradle"],
        ["gradle", "publish"],
        ["gradle", "test", "--tests"],
        ["mvn", "-s", "settings.xml", "test"],
    ],
)
def test_test_commands_cannot_smuggle_in_other_programs(tmp_path, argv):
    decision = CommandPolicy.default().evaluate(argv, tmp_path, tmp_path, frozenset())

    assert not decision.allowed, argv


# --------------------------------------------------------- versioning


def test_fingerprint_changes_with_content_and_with_new_files(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    first = workspace_version(tmp_path)

    (tmp_path / "a.py").write_text("x = 2\n")
    edited = workspace_version(tmp_path)
    (tmp_path / "b.py").write_text("y = 1\n")
    added = workspace_version(tmp_path)

    assert len({first["fingerprint"], edited["fingerprint"], added["fingerprint"]}) == 3
    assert first["kind"] == "files" and added["file_count"] == 2


def test_fingerprint_ignores_generated_clutter(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    before = workspace_version(tmp_path)

    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "a.cpython-313.pyc").write_bytes(b"\0")
    (tmp_path / ".pytest_cache").mkdir()
    (tmp_path / ".pytest_cache" / "v").write_text("x")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "m.js").write_text("x")
    (tmp_path / ".aseos_report_t.xml").write_text("<x/>")
    (tmp_path / ".coverage").write_bytes(b"1")

    assert workspace_version(tmp_path)["fingerprint"] == before["fingerprint"]


@pytest.mark.parametrize(
    "path, excluded",
    [
        ("src/__pycache__/a.pyc", True),
        ("a.pyc", True),
        (".aseos_report_x.xml", True),
        ("node_modules/x/y.js", True),
        ("src/a.py", False),
        ("src/pycache_notes.md", False),
        ("docs/report.xml", False),
    ],
)
def test_exclusion_rules(path, excluded):
    assert is_excluded(path) is excluded


def test_git_fingerprint_covers_committed_modified_and_untracked_files(tmp_path):
    from execution.tests.gitfixtures import git, init_repo, write

    repo = init_repo(tmp_path / "repo")
    clean = workspace_version(repo)
    write(repo, "a.txt", "changed\n")
    modified = workspace_version(repo)
    write(repo, "new.txt", "x\n")
    untracked = workspace_version(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "later")
    committed = workspace_version(repo)

    assert clean["kind"] == "git" and clean["head"]
    assert len({clean["fingerprint"], modified["fingerprint"], untracked["fingerprint"]}) == 3
    # same content, different commit: same fingerprint, different head
    assert committed["fingerprint"] == untracked["fingerprint"]
    assert committed["head"] != clean["head"]


def test_git_ignored_files_do_not_change_the_fingerprint(tmp_path):
    from execution.tests.gitfixtures import init_repo, write

    repo = init_repo(tmp_path / "repo")
    write(repo, ".gitignore", "build/\n")
    before = workspace_version(repo)

    write(repo, "build/out.bin", "generated")

    assert workspace_version(repo)["fingerprint"] == before["fingerprint"]
    assert os.name  # keep the import used on every platform


NODE_TAP = (
    "TAP version 13\n# Subtest: ok\nok 1 - ok\n  ---\n  duration_ms: 1.0\n  ...\n"
    "# Subtest: bad\nnot ok 2 - bad\n  ---\n  error: |-\n    boom\n  ...\n"
    "# Subtest: skipme\nok 3 - skipme # SKIP\n"
    "1..3\n# tests 3\n# suites 0\n# pass 1\n# fail 1\n# cancelled 0\n# skipped 1\n# todo 0\n"
)


def test_node_test_runner_tap_output_is_counted_with_its_cases():
    counts, cases = console.parse_node(NODE_TAP)

    assert (counts.total, counts.passed, counts.failed, counts.skipped) == (3, 1, 1, 1)
    assert {(c.name, c.status) for c in cases} == {
        ("ok", "passed"), ("bad", "failed"), ("skipme", "skipped"),
    }


def test_cancelled_node_tests_are_errors_and_a_lone_ok_line_is_not_a_summary():
    counts, _ = console.parse_node(NODE_TAP.replace("# cancelled 0", "# cancelled 2"))

    assert counts.errors == 2
    assert console.parse_node("ok 1 - looks like TAP\n") is None
    assert console.parse_node("# pass 3\n") is None  # no totals, no failure count
