"""Security qualification: attack the gateway the way a hostile or confused
model would, across every tool rather than one tool at a time, and check what
actually happened on disk.

The invariants are about effects, not messages:
  * a file outside the workspace is never changed, created or revealed;
  * a payload can never grant itself a capability;
  * a secret planted in the environment or in a secret file never appears in
    any result, artifact, record, journal or lease the system writes;
  * one task cannot write into another task's worktree through a tool.

What this suite cannot show is spelled out at the bottom: without a sandbox a
program the policy allows runs as the user, so it can reach anything the user
can. That is pinned by a test so the limit stays visible."""

import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from execution.artifacts import ArtifactStore
from execution.commands.policy import CommandPolicy, CommandRule, Match
from execution.gateway import ExecutionGateway
from execution.gateway.capabilities import (
    CAP_GIT_DISCARD,
    CAP_GIT_WRITE,
    CAP_SHELL_RUN,
    CAP_WORKTREE_MANAGE,
    DEFAULT_CAPABILITIES,
)
from execution.gateway.command_tools import RunCommandTool
from execution.gateway.tools import default_tools
from execution.tests import qualification_repos as repos
from execution.tests.gitfixtures import git, write
from execution.worktrees.manager import WorktreeManager
from runtime.schemas.action import Action

PY_POLICY = CommandPolicy(
    rules=[CommandRule("py", frozenset({"python"}), lambda args: Match("build"))]
)
ALL_CAPS = DEFAULT_CAPABILITIES | {
    CAP_SHELL_RUN, CAP_GIT_WRITE, CAP_GIT_DISCARD, CAP_WORKTREE_MANAGE, "worktree.discard", "fs.delete",
}
CANARY = "TOPSECRET-OUTSIDE-9f3a1c"


def tools():
    return [t for t in default_tools() if t.spec.name != "shell.run"] + [RunCommandTool(policy=PY_POLICY)]


def gateway(root, caps=ALL_CAPS, **kw):
    return ExecutionGateway(str(root), tools=tools(), granted_capabilities=caps, artifact_store=False, **kw)


def act(gw, action_type, payload=None, action_id="s-1"):
    return asyncio.run(gw.execute(Action(id=action_id, type=action_type, payload=payload or {})))


def tree_state(root: Path) -> dict[str, str]:
    """Every file under `root` (any depth) and its hash."""

    out = {}

    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()

    return out


def junction(link: Path, target: Path):
    if os.name == "nt":
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)

        if done.returncode != 0:
            pytest.skip("cannot create a junction here")
    else:
        os.symlink(target, link, target_is_directory=True)


@pytest.fixture
def world(tmp_path):
    """A repository plus a sibling directory that must stay out of reach."""

    repo = repos.python_project(tmp_path / "repo")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text(f"{CANARY}\n", encoding="utf-8")
    (outside / "keep").mkdir()
    (outside / "keep" / "file.txt").write_text("keep me\n", encoding="utf-8")
    junction(repo / "linked", outside)
    return repo, outside


# ------------------------------------------------- hostile paths, every tool


def hostile_paths(repo: Path, outside: Path) -> list[str]:
    paths = [
        "../outside/secret.txt",
        "..\\outside\\secret.txt",
        "sub/../../outside/secret.txt",
        str(outside / "secret.txt"),
        str(outside / "new.txt"),
        outside.as_posix() + "/new.txt",
        "linked/secret.txt",  # through a junction/symlink that points outside
        "linked/new.txt",
        ".git/config",
        ".git/hooks/pre-commit",
        "calc.py\x00.txt",
        "calc.py:evil_stream",
        "./../repo/../outside/secret.txt",
    ]

    if os.name == "nt":
        paths += ["\\\\localhost\\c$\\Windows\\win.ini", "NUL", "CON", "C:\\Windows\\win.ini", "..\\..\\..\\Windows\\win.ini"]
    else:
        paths += ["/etc/passwd", "/proc/self/environ", "~/.ssh/id_rsa"]

    return paths


def payloads_for(path: str) -> list[tuple[str, dict]]:
    """One minimal call per path-taking tool with `path` in the dangerous slot."""

    create_patch = f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+pwned\n"
    edit_patch = f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-x\n+pwned\n"
    return [
        ("READ", {"path": path}),
        ("LIST_DIR", {"path": path}),
        ("FILE_INFO", {"path": path}),
        ("SEARCH", {"query": "TOPSECRET", "path": path}),
        ("EDIT", {"file": path, "old": "TOPSECRET", "new": "pwned"}),
        ("WRITE", {"path": path, "content": "pwned\n"}),
        ("WRITE", {"path": path, "content": "pwned\n", "expected_sha256": "0" * 64}),
        ("VALIDATE_PATCH", {"patch": create_patch}),
        ("APPLY_PATCH", {"patch": create_patch}),
        ("APPLY_PATCH", {"patch": edit_patch}),
        ("GIT_DIFF", {"paths": [path]}),
        ("GIT_STAGE", {"paths": [path]}),
        ("GIT_COMMIT", {"message": "x", "paths": [path]}),
        ("GIT_DISCARD", {"paths": [path]}),
        ("RUN_TEST", {"targets": [path]}),
        ("RUN_COMMAND", {"command": ["python", "-c", "pass"], "cwd": path}),
        ("READ_ARTIFACT", {"id": path}),
        ("WORKTREE_CREATE", {"task_id": path}),
        ("WORKTREE_REMOVE", {"worktree_id": path}),
    ]


def test_no_tool_reads_writes_or_reveals_anything_outside_the_workspace(world):
    repo, outside = world
    gw = gateway(repo)
    outside_before = tree_state(outside)
    git_before = tree_state(repo / ".git")
    inside_before = {k: v for k, v in tree_state(repo).items() if not k.startswith((".git/", "linked/"))}
    leaks, attempts = [], 0

    for path in hostile_paths(repo, outside):
        for action_type, payload in payloads_for(path):
            attempts += 1
            result = act(gw, action_type, payload, f"s-{attempts}")
            visible = json.dumps(result.model_dump(), default=str)

            if CANARY in visible:
                leaks.append((action_type, path, "outside content revealed"))

            if "Windows" in visible and "[fonts]" in visible:  # contents of win.ini
                leaks.append((action_type, path, "system file revealed"))

    assert attempts > 200  # the sweep really ran
    assert not leaks, leaks
    assert tree_state(outside) == outside_before, "a file outside the workspace changed or appeared"
    assert not (outside / "new.txt").exists()
    git_after = tree_state(repo / ".git")
    # Git itself writes its index and objects while the sweep runs; what must not change
    # is the configuration and anything that could run code later.
    assert git_after.get("config") == git_before.get("config"), ".git/config was altered"
    hooks = lambda state: {k for k in state if k.startswith("hooks/") and not k.endswith(".sample")}
    assert hooks(git_after) == hooks(git_before), "a Git hook appeared or changed"
    now = {k: v for k, v in tree_state(repo).items() if not k.startswith((".git/", "linked/"))}
    assert {k: v for k, v in now.items() if k in inside_before and v != inside_before[k]} == {}, "a tracked file was changed"


# ------------------------------------------------------- permission bypass

SELF_GRANTS = {
    "capabilities": ["shell.run", "git.write", "fs.read_secrets", "worktree.discard"],
    "granted_capabilities": ["shell.run"],
    "level": "ELEVATED",
    "max_level": "ELEVATED",
    "profile": "elevated",
    "discard_confirmed": True,
    "discard_unreviewed": True,
    "force": True,
    "sandbox": False,
    "require_sandbox": False,
    "workspace": "/",
    "workspace_path": "/",
    "admin": True,
}

CHANGING = [
    ("WRITE", {"path": "new.txt", "content": "x\n"}),
    ("EDIT", {"file": "calc.py", "old": "a + b", "new": "b + a"}),
    ("APPLY_PATCH", {"patch": "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a + b\n+    return b + a\n"}),
    ("GIT_STAGE", {"paths": ["calc.py"]}),
    ("GIT_COMMIT", {"message": "m", "paths": ["calc.py"]}),
    ("GIT_BRANCH", {"name": "evil"}),
    ("GIT_DISCARD", {"paths": ["calc.py"]}),
    ("RUN_COMMAND", {"command": ["python", "-c", "open('pwned.txt','w').write('x')"]}),
    ("WORKTREE_CREATE", {"task_id": "evil"}),
    ("WORKTREE_REMOVE", {"worktree_id": "wt-000000000000", "discard_unreviewed": True}),
]


@pytest.mark.parametrize("action_type,payload", CHANGING, ids=[a for a, _ in CHANGING])
def test_a_payload_cannot_grant_itself_anything(tmp_path, action_type, payload):
    repo = repos.python_project(tmp_path / "repo")
    before = tree_state(repo)
    head = git(repo, "rev-parse", "HEAD")
    gw = gateway(repo, caps=frozenset(), profile="read_only")  # nothing granted; read-only profile

    plain = act(gw, action_type, payload)
    spiked = act(gw, action_type, {**payload, **SELF_GRANTS}, "s-2")

    assert plain.error_category == "PERMISSION_DENIED"
    assert spiked.error_category == "PERMISSION_DENIED", spiked.stderr
    assert spiked.data["approvable"] is False  # the profile ceiling cannot be talked around
    assert tree_state(repo) == before
    assert git(repo, "rev-parse", "HEAD") == head
    assert not (repo / "pwned.txt").exists()
    assert "evil" not in git(repo, "branch", "--list")


def test_secret_files_stay_unreadable_without_the_secret_capability_whatever_the_payload_says(tmp_path):
    repo = repos.python_project(tmp_path / "repo")
    write(repo, ".env", f"DB_PASSWORD={CANARY}\n")
    write(repo, "config/credentials.json", json.dumps({"token": CANARY}))
    write(repo, "id_rsa", f"-----BEGIN PRIVATE KEY-----\n{CANARY}\n")
    gw = gateway(repo, caps=DEFAULT_CAPABILITIES)
    seen = []

    for name in (".env", "config/credentials.json", "id_rsa"):
        for extra in ({}, SELF_GRANTS, {"include_secrets": True, "allow_secrets": True}):
            seen.append(act(gw, "READ", {"path": name, **extra}))
            seen.append(act(gw, "SEARCH", {"query": "TOPSECRET", "path": name, **extra}))
            seen.append(act(gw, "FILE_INFO", {"path": name, **extra}))

    seen.append(act(gw, "SEARCH", {"query": "TOPSECRET"}))  # a whole-tree search must skip them too
    write(repo, ".env", f"DB_PASSWORD={CANARY}-changed\n")
    seen.append(act(gw, "GIT_DIFF", {"include_untracked": True}))

    assert all(CANARY not in json.dumps(r.model_dump(), default=str) for r in seen)


# --------------------------------------------------------- secret leakage


def files_under(*roots: Path):
    for root in roots:
        for path in root.rglob("*"):
            if path.is_file():
                yield path


def test_a_planted_secret_appears_in_no_result_artifact_record_journal_or_lease(tmp_path, monkeypatch):
    env_secret = "ENVSECRET-" + "7d2b9e41c0aa"
    explicit_secret = "EXPLICITSECRET-" + "3c91f0b2d7e8"
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", env_secret)
    repo = repos.python_project(tmp_path / "repo")
    write(repo, ".env", f"DB_PASSWORD={CANARY}\n")
    state, store = tmp_path / "state", tmp_path / "store"
    gw = ExecutionGateway(
        str(repo), tools=tools(), granted_capabilities=ALL_CAPS, record_reads=True,
        artifact_store=ArtifactStore(store), state_dir=state, lease=True,
    )
    results = []

    dump_env = "import os,sys; sys.stdout.write(repr(sorted(os.environ.items())))"
    results.append(act(gw, "RUN_COMMAND", {"command": ["python", "-c", dump_env]}, "e-1"))
    results.append(
        act(gw, "RUN_COMMAND", {"command": ["python", "-c", "import os; print(os.environ['MY_SECRET'])"], "env": {"MY_SECRET": explicit_secret}}, "e-2")
    )
    results.append(
        act(gw, "RUN_COMMAND", {"command": ["python", "-c", f"import sys; sys.stderr.write('token {explicit_secret}'); sys.exit(2)"], "env": {"MY_SECRET": explicit_secret}}, "e-3")
    )
    results.append(act(gw, "READ", {"path": ".env"}, "e-4"))
    results.append(act(gw, "WRITE", {"path": "notes.txt", "content": "hello\n"}, "e-5"))
    results.append(act(gw, "GIT_DIFF", {}, "e-6"))
    results.append(act(gw, "RUN_TEST", {}, "e-7"))
    listing = act(gw, "LIST_ARTIFACTS", {}, "e-8")
    results.append(listing)

    for ref in listing.data.get("artifacts", []):
        results.append(act(gw, "READ_ARTIFACT", {"id": ref["id"]}, f"r-{ref['id']}"))

    blob = json.dumps([r.model_dump() for r in results], default=str)
    assert env_secret not in blob, "a parent-process secret reached a child process or its result"
    assert explicit_secret not in blob, "an explicitly passed secret came back unmasked in a result"
    assert CANARY not in blob, "a secret-file value reached a result"

    gw.release_lease()
    leaked = []

    for path in files_under(store, state, repo / ".git" / "aseos"):
        data = path.read_bytes()

        for name, secret in (("env", env_secret), ("explicit", explicit_secret), ("file", CANARY)):
            if secret.encode() in data:
                leaked.append((name, str(path.relative_to(tmp_path))))

    assert not leaked, leaked


# ------------------------------------------------------------- cross-task


@pytest.fixture
def two_tasks(tmp_path):
    repo = repos.python_project(tmp_path / "repo")
    manager = WorktreeManager(repo)
    a, b = manager.create("task-a"), manager.create("task-b")
    (Path(b.path) / "b_only.txt").write_text("belongs to B\n", encoding="utf-8")
    return repo, manager, a, b


def test_one_task_cannot_write_read_or_commit_into_anothers_worktree(two_tasks):
    repo, manager, a, b = two_tasks
    gw_a = gateway(a.path)
    b_path = Path(b.path)
    before = tree_state(b_path)
    head_b = git(b_path, "rev-parse", "HEAD")
    names = [
        "../task-b/b_only.txt", "..\\task-b\\b_only.txt", str(b_path / "b_only.txt"),
        "../task-b/planted.txt", str(b_path / "planted.txt"), "../task-b/calc.py",
    ]
    revealed = []

    for index, name in enumerate(names):
        for action_type, payload in payloads_for(name):
            if action_type in ("WORKTREE_CREATE", "WORKTREE_REMOVE", "READ_ARTIFACT"):
                continue

            result = act(gw_a, action_type, payload, f"x-{index}-{action_type}")

            if "belongs to B" in json.dumps(result.model_dump(), default=str):
                revealed.append((action_type, name))

    assert not revealed, revealed
    assert tree_state(b_path) == before
    assert not (b_path / "planted.txt").exists()
    assert git(b_path, "rev-parse", "HEAD") == head_b


def test_one_task_cannot_remove_or_reach_another_tasks_worktree_through_the_registry(two_tasks):
    repo, manager, a, b = two_tasks
    gw_a = gateway(a.path)  # a gateway bound to A's worktree

    result = act(gw_a, "WORKTREE_REMOVE", {"worktree_id": b.worktree_id})

    assert not result.success  # worktree management only works from the main checkout
    assert Path(b.path, "b_only.txt").exists()
    assert manager.get(b.worktree_id).status == "active"


def test_a_tampered_registry_cannot_aim_removal_at_another_directory(tmp_path):
    repo = repos.python_project(tmp_path / "repo")
    manager = WorktreeManager(repo)
    record = manager.create("victim")
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "life_work.txt").write_text("irreplaceable\n", encoding="utf-8")

    for forged_path in (str(precious), str(repo), str(tmp_path), str(repo / ".git")):
        path = repo / ".git" / "aseos" / "worktrees" / f"{record.worktree_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["path"] = forged_path
        path.write_text(json.dumps(data), encoding="utf-8")

        result = act(gateway(repo), "WORKTREE_REMOVE", {"worktree_id": record.worktree_id, "discard_unreviewed": True})

        assert not result.success, forged_path
        assert (precious / "life_work.txt").read_text() == "irreplaceable\n"
        assert (repo / "calc.py").exists() and (repo / ".git").is_dir()


# ------------------------------------------------ what this suite cannot show


def test_known_limit_without_a_sandbox_an_allowed_program_runs_as_the_user(two_tasks):
    """Not a bug to fix here: it is the documented boundary. The tools above are
    confined; a program the policy lets through is not, unless a sandbox runs it.
    If this test starts failing because programs became confined, update
    SECURITY_MODEL.md section 6 together with it."""

    repo, manager, a, b = two_tasks
    target = Path(b.path) / "written_by_a_program.txt"
    code = f"open({str(target)!r}, 'w').write('escaped')"
    result = act(gateway(a.path), "RUN_COMMAND", {"command": ["python", "-c", code]})

    assert result.success
    assert target.exists(), "programs are now confined; update the documentation"
