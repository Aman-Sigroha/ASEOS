"""Throwaway repositories shaped like real work, for the qualification tests.

Each builder returns the repository root (a real Git repo with one commit,
plus whatever uncommitted state the scenario needs). They are small on
purpose; sizes for the benchmarks come from `big_repo`.
"""

import json
import shutil
from pathlib import Path

from execution.tests.gitfixtures import git, init_repo, write

CALC = "def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return a * b\n"
CALC_TEST = (
    "from calc import add, mul\n\n\n"
    "def test_add():\n    assert add(2, 3) == 5\n\n\n"
    "def test_mul():\n    assert mul(2, 3) == 6\n"
)


def python_project(root: Path) -> Path:
    repo = init_repo(root, commit=False)
    write(repo, "calc.py", CALC)
    write(repo, "test_calc.py", CALC_TEST)
    git(repo, "add", ".")
    git(repo, "commit", "-m", "calculator")
    return repo


def dirty_tree(root: Path) -> Path:
    """A project where the user has unfinished work: an edited tracked file, a
    staged edit, an untracked file and an ignored file."""

    repo = python_project(root)
    write(repo, ".gitignore", "*.log\n")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-m", "ignore logs")
    write(repo, "calc.py", CALC + "\n\ndef sub(a, b):\n    return a - b\n")  # unstaged
    write(repo, "notes.txt", "staged notes\n")
    git(repo, "add", "notes.txt")  # staged
    write(repo, "scratch.txt", "mine, not committed\n")  # untracked
    write(repo, "debug.log", "ignored\n")  # ignored
    return repo


def failing_build(root: Path) -> Path:
    """Source that does not even import, so no test can run."""

    repo = python_project(root)
    write(repo, "calc.py", "def add(a, b)\n    return a + b\n")  # missing colon
    git(repo, "add", ".")
    git(repo, "commit", "-m", "broken build")
    return repo


def failing_tests(root: Path) -> Path:
    repo = python_project(root)
    write(repo, "calc.py", CALC.replace("a * b", "a + b"))
    git(repo, "add", ".")
    git(repo, "commit", "-m", "mul is wrong")
    return repo


def node_project(root: Path, *, passing: bool = True) -> Path:
    """A real Node project using the built-in test runner (`node --test`)."""

    repo = init_repo(root, commit=False)
    write(repo, "package.json", json.dumps({"name": "q", "version": "1.0.0", "scripts": {"test": "node --test"}}))
    expected = "2" if passing else "3"
    write(
        repo,
        "sum.test.js",
        "const test = require('node:test');\nconst assert = require('node:assert');\n"
        "test('adds', () => assert.strictEqual(1 + 1, " + expected + "));\n"
        "test('skipped', { skip: true }, () => {});\n",
    )
    git(repo, "add", ".")
    git(repo, "commit", "-m", "node project")
    return repo


def polyglot(root: Path) -> Path:
    """Python tests and Node tests (and a Go module) in one repository."""

    repo = python_project(root)
    write(repo, "web/package.json", json.dumps({"scripts": {"test": "node --test"}}))
    write(repo, "package.json", json.dumps({"scripts": {"test": "node --test web"}}))
    write(
        repo,
        "web/ui.test.js",
        "const test = require('node:test');\nconst assert = require('node:assert');\n"
        "test('ui is broken', () => assert.fail('the Node side fails'));\n",
    )
    write(repo, "go.mod", "module example.com/q\n\ngo 1.21\n")
    write(repo, "q_test.go", "package q\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "polyglot")
    return repo


def awkward_files(root: Path) -> Path:
    """Names and encodings that break naive tooling: spaces, unicode, CRLF,
    no trailing newline, a binary file, a very long line."""

    repo = init_repo(root, commit=False)
    (repo / "dir with space").mkdir()
    write(repo, "dir with space/file name.txt", "spaced\n")
    write(repo, "ünïcode-файл.txt", "unicode name\n")
    (repo / "crlf.txt").write_bytes(b"one\r\ntwo\r\nthree\r\n")
    (repo / "no_newline.txt").write_bytes(b"last line without newline")
    (repo / "blob.bin").write_bytes(bytes(range(256)) * 8)
    write(repo, "wide.txt", "x" * 200_000 + "\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "awkward files")
    return repo


def big_repo(root: Path, files: int = 2000, lines: int = 20) -> Path:
    """Many small tracked files, written without one `git add` per file."""

    repo = init_repo(root, commit=False)
    body = "".join(f"line {i}\n" for i in range(lines))

    for index in range(files):
        folder = repo / f"pkg{index % 40}"
        folder.mkdir(exist_ok=True)
        (folder / f"mod{index}.txt").write_text(f"# {index}\n{body}", encoding="utf-8")

    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", f"{files} files")
    return repo


def copy_repo(source: Path, target: Path) -> Path:
    shutil.copytree(source, target)
    return target
