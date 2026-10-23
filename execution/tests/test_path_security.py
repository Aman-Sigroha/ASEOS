import os

import pytest

from execution.gateway import ExecutionGateway
from execution.gateway.capabilities import CAP_FS_READ_SECRETS, DEFAULT_CAPABILITIES
from execution.gateway.tools import default_tools
from execution.security.paths import (
    is_secret_path,
    raw_path_problem,
    read_denied_reason,
    write_denied_reason,
)
from execution.tests.gitfixtures import git, init_repo, write
from runtime.schemas.action import Action

WINDOWS = os.name == "nt"


@pytest.fixture
def workspace(tmp_path):
    root = (tmp_path / "ws").resolve()
    root.mkdir()
    return root


def make_gateway(root, extra=frozenset()):
    return ExecutionGateway(
        str(root),
        tools=default_tools(),
        granted_capabilities=DEFAULT_CAPABILITIES | set(extra),
    )


async def run(gateway, action_type, payload=None):
    return await gateway.execute(
        Action(id="p-1", type=action_type, payload=payload or {})
    )


# ------------------------------------------------------- path rules table


@pytest.mark.parametrize(
    "path, secret",
    [
        (".env", True),
        (".ENV", True),
        (".env.local", True),
        (".env.production", True),
        (".env.example", False),
        (".env.sample", False),
        ("config/.env", True),
        ("server.pem", True),
        ("keys/private.KEY", True),
        ("cert.p12", True),
        (".npmrc", True),
        (".pypirc", True),
        (".netrc", True),
        ("id_rsa", True),
        (".ssh/config", True),
        ("home/.aws/credentials", True),
        ("terraform.tfstate", True),
        ("prod.tfvars", True),
        ("README.md", False),
        ("src/env.py", False),
        ("environment.yml", False),
        ("docs/keys.md", False),
        ("id_rsa.pub.md", False),
    ],
)
def test_secret_path_classification(path, secret):
    assert is_secret_path(path) is secret


@pytest.mark.parametrize(
    "path",
    [".git/config", ".GIT/hooks/x", "a/.git/b", ".env", ".ENV.local", ".aseos_worktrees/t/x"],
)
def test_write_protected_paths(path):
    assert write_denied_reason(path) is not None


@pytest.mark.parametrize("path", ["src/a.py", ".env.example", ".gitignore", "git/x", ".github/ci.yml"])
def test_writable_paths(path):
    assert write_denied_reason(path) is None


def test_read_policy_has_an_explicit_override():
    assert read_denied_reason(".env") is not None
    assert read_denied_reason(".env", allow_secrets=True) is None
    assert read_denied_reason(".aseos_worktrees/t/x", allow_secrets=True) is not None


@pytest.mark.parametrize("raw", ["a\0b", "a\nb", "a\tb", 5, None])
def test_malformed_paths_on_every_platform(raw):
    assert raw_path_problem(raw)


# -------------------------------------------- Windows path tricks (nt only)


@pytest.mark.skipif(not WINDOWS, reason="Windows path semantics")
@pytest.mark.parametrize(
    "path",
    [
        ".env::$DATA",
        ".env:stream",
        "ok.txt:hidden",
        "CON",
        "nul",
        "aux.txt",
        "COM1",
        "dir/LPT1.txt",
        "con.tar.gz",
        "a<b.txt",
        'a"b.txt',
        "what?.txt",
        "star*.txt",
        "pipe|x",
    ],
)
@pytest.mark.asyncio
async def test_windows_specific_names_are_rejected(workspace, path):
    result = await run(make_gateway(workspace), "WRITE", {"path": path, "content": "x"})

    assert result.success is False
    assert result.error_category == "INVALID_ARGUMENTS"
    assert list(workspace.iterdir()) == []


@pytest.mark.skipif(not WINDOWS, reason="Windows path semantics")
@pytest.mark.parametrize(
    "path",
    [
        ".GIT/hooks/x",
        ".Git/config2",
        ".git./hooks/y",
        ".git /hooks/z",
        ".git\\hooks\\w",
        "a/../.git/hooks/r",
        ".ENV",
        ".env.",
        ".env ",
        "GIT~1/hooks/q",
    ],
)
@pytest.mark.asyncio
async def test_windows_aliases_of_protected_names_are_blocked(workspace, path):
    (workspace / ".git").mkdir()
    (workspace / ".git" / "config").write_text("orig")
    (workspace / ".env").write_text("SECRET=1")

    for action_type, payload in (
        ("WRITE", {"path": path, "content": "pwn"}),
        ("APPLY_PATCH", {"patch": f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+pwn\n"}),
    ):
        result = await run(make_gateway(workspace), action_type, payload)
        assert result.success is False, (action_type, path)

    assert (workspace / ".git" / "config").read_text() == "orig"
    assert (workspace / ".env").read_text() == "SECRET=1"
    assert sorted(p.name for p in (workspace / ".git").iterdir()) == ["config"]


@pytest.mark.asyncio
async def test_traversal_in_every_spelling_stays_denied(workspace, tmp_path):
    (tmp_path / "outside.txt").write_text("secret")
    gateway = make_gateway(workspace)

    for path in ("../outside.txt", "..\\outside.txt", "a/../../outside.txt", str(tmp_path / "outside.txt")):
        read = await run(gateway, "READ", {"path": path})
        wrote = await run(gateway, "WRITE", {"path": path, "content": "x"})

        assert read.error_category in ("PERMISSION_DENIED", "INVALID_ARGUMENTS"), path
        assert wrote.success is False, path

    assert (tmp_path / "outside.txt").read_text() == "secret"


# ------------------------------------------------- secret content stays hidden


@pytest.mark.asyncio
async def test_secret_files_cannot_be_read(workspace):
    (workspace / ".env").write_text("API_KEY=hunter2\n")
    (workspace / "server.pem").write_text("-----BEGIN PRIVATE KEY-----\n")
    (workspace / ".ssh").mkdir()
    (workspace / ".ssh" / "id_rsa").write_text("key")
    gateway = make_gateway(workspace)

    for path in (".env", "server.pem", ".ssh/id_rsa"):
        result = await run(gateway, "READ", {"path": path})

        assert result.error_category == "PERMISSION_DENIED", path
        assert "hunter2" not in result.stdout + result.stderr + str(result.data)
        assert "fs.read_secrets" in result.stderr


@pytest.mark.asyncio
async def test_secret_read_needs_an_explicit_capability(workspace):
    (workspace / ".env").write_text("API_KEY=hunter2\n")

    result = await run(
        make_gateway(workspace, {CAP_FS_READ_SECRETS}), "READ", {"path": ".env"}
    )

    assert result.success is True
    assert "hunter2" in result.stdout


@pytest.mark.asyncio
async def test_templates_remain_readable(workspace):
    (workspace / ".env.example").write_text("API_KEY=changeme\n")

    result = await run(make_gateway(workspace), "READ", {"path": ".env.example"})

    assert result.success is True


@pytest.mark.asyncio
async def test_file_info_reports_existence_but_never_a_hash_of_a_secret(workspace):
    (workspace / ".env").write_text("API_KEY=hunter2\n")

    result = await run(make_gateway(workspace), "FILE_INFO", {"path": ".env"})

    assert result.success is True
    assert result.data["exists"] is True
    assert "sha256" not in result.data
    assert "restricted" in result.data


@pytest.mark.asyncio
async def test_search_skips_secret_files_and_says_so(workspace):
    (workspace / ".env").write_text("TOKEN=findme-secret\n")
    (workspace / "app.py").write_text("# findme\n")

    result = await run(make_gateway(workspace), "SEARCH", {"query": "findme"})

    assert "app.py" in result.stdout
    assert "secret" not in result.stdout
    assert ".env" not in result.stdout
    assert result.data["skipped_sensitive_files"] == 1


@pytest.mark.asyncio
async def test_searching_one_secret_file_directly_is_still_blocked(workspace):
    (workspace / ".env").write_text("TOKEN=findme-secret\n")

    result = await run(
        make_gateway(workspace), "SEARCH", {"query": "findme", "path": ".env"}
    )

    assert "findme-secret" not in result.stdout
    assert result.data["skipped_sensitive_files"] == 1


@pytest.mark.asyncio
async def test_patches_cannot_read_secrets_through_conflict_details(workspace):
    (workspace / "server.pem").write_text("line-one-secret\n")
    patch = "--- a/server.pem\n+++ b/server.pem\n@@ -1 +1 @@\n-guess\n+new\n"

    for action_type in ("VALIDATE_PATCH", "APPLY_PATCH"):
        result = await run(make_gateway(workspace), action_type, {"patch": patch})

        assert result.error_category == "PERMISSION_DENIED"
        assert "line-one-secret" not in str(result.data) + result.stderr


# ------------------------------------------------------------ git diff


@pytest.fixture
def repo(tmp_path):
    root = init_repo(tmp_path / "repo", commit=False)
    write(root, "app.py", "x = 1\n")
    write(root, ".env", "TOKEN=old-secret\n")
    write(root, "server.pem", "pem-old\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "init")
    return root


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["unstaged", "staged"])
async def test_git_diff_never_shows_secret_content(repo, mode):
    write(repo, "app.py", "x = 2\n")
    write(repo, ".env", "TOKEN=new-secret\n")
    write(repo, "server.pem", "pem-new\n")
    write(repo, "untracked.key", "untracked-secret\n")

    if mode == "staged":
        git(repo, "add", "app.py", ".env", "server.pem")

    result = await run(make_gateway(repo), "GIT_DIFF", {"mode": mode})
    shown = result.stdout + str(result.data)

    assert result.success is True, result.stderr
    assert "+x = 2" in result.stdout
    for secret in ("old-secret", "new-secret", "pem-old", "pem-new", "untracked-secret"):
        assert secret not in shown, secret
    redacted = {f["path"] for f in result.data["files"] if f.get("redacted")}
    assert {".env", "server.pem"} <= redacted


@pytest.mark.asyncio
async def test_git_diff_can_show_secrets_with_the_capability(repo):
    write(repo, ".env", "TOKEN=new-secret\n")

    result = await run(
        make_gateway(repo, {CAP_FS_READ_SECRETS}), "GIT_DIFF", {"mode": "unstaged"}
    )

    assert "+TOKEN=new-secret" in result.stdout


@pytest.mark.asyncio
async def test_git_diff_with_only_secret_changes_shows_nothing(repo):
    write(repo, ".env", "TOKEN=new-secret\n")

    result = await run(make_gateway(repo), "GIT_DIFF")

    assert result.stdout == ""
    assert result.data["files"][0]["redacted"] is True


@pytest.mark.asyncio
async def test_secret_files_cannot_be_staged(repo):
    write(repo, ".env", "TOKEN=new-secret\n")
    gateway = make_gateway(repo, {"git.write"})

    result = await run(gateway, "GIT_STAGE", {"paths": [".env"]})

    assert result.error_category == "PERMISSION_DENIED"
    assert git(repo, "diff", "--cached", "--name-only") == ""


# ------------------------------------------------------- managed area


@pytest.mark.asyncio
async def test_other_tasks_worktrees_are_off_limits_to_file_tools(tmp_path):
    repo = init_repo(tmp_path / "repo")
    other = repo / ".aseos_worktrees" / "other-task"
    other.mkdir(parents=True)
    (other / "secret_work.py").write_text("# other task's work\n")
    gateway = make_gateway(repo)

    read = await run(gateway, "READ", {"path": ".aseos_worktrees/other-task/secret_work.py"})
    info = await run(gateway, "FILE_INFO", {"path": ".aseos_worktrees/other-task/secret_work.py"})
    listing = await run(gateway, "LIST_DIR", {"path": ".aseos_worktrees"})
    root = await run(gateway, "LIST_DIR", {"path": "."})
    search = await run(gateway, "SEARCH", {"query": "other task"})
    wrote = await run(
        gateway, "WRITE", {"path": ".aseos_worktrees/other-task/x.py", "content": "pwn"}
    )
    patched = await run(
        gateway,
        "APPLY_PATCH",
        {"patch": "--- a/.aseos_worktrees/other-task/secret_work.py\n"
                  "+++ b/.aseos_worktrees/other-task/secret_work.py\n"
                  "@@ -1 +1 @@\n-# other task's work\n+# pwned\n"},
    )

    assert read.error_category == "PERMISSION_DENIED"
    assert info.error_category == "PERMISSION_DENIED"
    assert listing.error_category == "PERMISSION_DENIED"
    assert ".aseos_worktrees" not in [e["name"] for e in root.data["entries"]]
    assert "other task" not in search.stdout
    assert wrote.error_category == "PERMISSION_DENIED"
    assert patched.error_category == "PERMISSION_DENIED"
    assert (other / "secret_work.py").read_text() == "# other task's work\n"


@pytest.mark.asyncio
async def test_search_works_when_the_workspace_sits_under_an_ignored_directory(tmp_path):
    nested = tmp_path / "node_modules" / "venv" / "project"
    nested.mkdir(parents=True)
    (nested / "app.py").write_text("# needle\n")
    (nested / "node_modules").mkdir()
    (nested / "node_modules" / "dep.py").write_text("# needle\n")

    result = await run(make_gateway(nested.resolve()), "SEARCH", {"query": "needle"})

    assert "app.py" in result.stdout
    assert "dep.py" not in result.stdout
