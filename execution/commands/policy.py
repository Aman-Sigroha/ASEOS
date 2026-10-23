"""Default-deny command policy, enforced in trusted code.

A command runs only if (1) its executable is a bare name matching a rule,
(2) its arguments match that rule's allowed forms, (3) every path-like
argument stays inside the workspace, and (4) the caller holds every
capability the rule demands. Shells, destructive utilities, network
clients and `git` (use the git.* tools) are refused outright.

Limits worth knowing: a policy decides what may be *started*. It cannot see
what an allowed program then does (a test suite is arbitrary code, and
nothing here blocks its network or filesystem access). That is the job of
the sandbox (Phase 7).
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

CAP_SHELL_RUN = "shell.run"
CAP_SHELL_NETWORK = "shell.network"

MAX_TIMEOUT_SECONDS = 600.0
DEFAULT_TIMEOUT_SECONDS = 60.0
MAX_ARGS = 64
MAX_ARG_CHARS = 2000


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    rule: str | None = None
    category: str | None = None  # test | lint | typecheck | build | install | inspect
    risk: str = "low"  # low | medium | high
    required_capabilities: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Match:
    category: str
    risk: str = "low"
    required_capabilities: frozenset[str] = frozenset()


# A matcher returns a Match (allowed), a str (denied, with that reason) or
# None (arguments do not fit any allowed form).
Matcher = Callable[[list[str]], "Match | str | None"]


@dataclass(frozen=True)
class CommandRule:
    name: str
    executables: frozenset[str]
    matcher: Matcher
    description: str = ""


_STRIP_SUFFIXES = (".exe", ".cmd", ".bat", ".com", ".ps1")

DENIED_EXECUTABLES: dict[str, str] = {
    **{
        name: "shells are not available; run the program directly"
        for name in (
            "sh", "bash", "zsh", "fish", "dash", "ksh", "csh", "tcsh", "cmd",
            "powershell", "pwsh", "wsl", "busybox", "env", "xargs", "nohup",
            "start", "call",
        )
    },
    **{
        name: "destructive or system-level command"
        for name in (
            "rm", "rmdir", "del", "erase", "rd", "format", "mkfs", "dd",
            "shutdown", "reboot", "halt", "poweroff", "sudo", "su", "doas",
            "runas", "chmod", "chown", "chattr", "icacls", "takeown", "mount",
            "umount", "reg", "regedit", "schtasks", "crontab", "kill", "pkill",
            "killall", "taskkill", "mv", "move", "cp", "copy", "ln", "mklink",
            "truncate", "shred", "find", "sed", "awk", "tee",
        )
    },
    **{
        name: "network or remote-access tool"
        for name in (
            "curl", "wget", "nc", "ncat", "netcat", "telnet", "ssh", "scp",
            "sftp", "ftp", "rsync", "invoke-webrequest", "certutil",
            "bitsadmin",
        )
    },
    "git": "use the git.* tools (git.status, git.diff, git.commit, ...)",
    "docker": "container tools are not available",
    "podman": "container tools are not available",
    "kubectl": "cluster tools are not available",
}


def executable_name(argv0: str) -> str:
    name = argv0.replace("\\", "/").rsplit("/", 1)[-1].lower()

    for suffix in _STRIP_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]

    return name


# ---------------------------------------------------------------- matchers

_PYTHON_NAME = re.compile(r"^(python(\d+(\.\d+)*)?|py)$")
_PYTHON_MODULES = {
    "pytest": "test",
    "unittest": "test",
    "mypy": "typecheck",
    "pyflakes": "lint",
    "flake8": "lint",
    "ruff": "lint",
    "black": "lint",
    "compileall": "build",
}
_VERSION_FLAGS = {"--version", "-V", "-v", "version"}


def _no_fix(args: list[str]) -> str | None:
    for arg in args:
        if arg in ("--fix", "--unsafe-fixes", "--write", "-w") or arg.startswith(
            "--fix="
        ):
            return f"'{arg}' modifies files; not allowed"

    return None


def _tool_args(tool: str, args: list[str]) -> Match | str | None:
    """Arguments of pytest / unittest / ruff / mypy / flake8 / black ..."""

    category = _PYTHON_MODULES[tool]
    reason = _no_fix(args)

    if reason:
        return reason

    if tool == "pytest":
        for arg in args:
            if arg.startswith("-p") and not arg.startswith("--"):
                return "loading pytest plugins by name is not allowed"

            if arg in ("-o", "--override-ini") or arg.startswith("--override-ini="):
                return "overriding pytest ini options is not allowed"

    if tool == "black" and not any(a in ("--check", "--diff") for a in args):
        return "black must be run with --check or --diff"

    if tool == "ruff" and args[:1] == ["format"] and not any(
        a in ("--check", "--diff") for a in args
    ):
        return "ruff format must be run with --check or --diff"

    return Match(category)


def _python_matcher(args: list[str]) -> Match | str | None:
    if args and args[0] in _VERSION_FLAGS and len(args) == 1:
        return Match("inspect")

    if len(args) >= 2 and args[0] == "-m":
        module = args[1]

        if module in _PYTHON_MODULES:
            return _tool_args(module, args[2:])

        if module == "pip":
            sub = args[2] if len(args) > 2 else ""

            if sub in ("list", "freeze", "show", "check", "--version"):
                return Match("inspect")

            if sub in ("install", "download", "uninstall", "wheel"):
                return Match(
                    "install", "high", frozenset({CAP_SHELL_NETWORK})
                )

            return "this pip subcommand is not allowed"

        return f"python module '{module}' is not allowed"

    if args[:1] == ["-c"]:
        return "inline code (-c) is not allowed"

    return "only 'python -m <pytest|unittest|mypy|ruff|flake8|black|compileall>' is allowed"


def _direct_tool(tool: str) -> Matcher:
    def matcher(args: list[str]) -> Match | str | None:
        if args and args[0] in _VERSION_FLAGS and len(args) == 1:
            return Match("inspect")

        return _tool_args(tool, args)

    return matcher


_NPM_SCRIPTS = {"test", "lint", "build", "typecheck", "check", "format:check"}
_NPM_INSTALL = {"install", "i", "ci", "add"}


def _npm_matcher(args: list[str]) -> Match | str | None:
    if not args:
        return "a subcommand is required"

    if args == ["--version"] or args == ["-v"]:
        return Match("inspect")

    sub = args[0]
    rest = args[1:]

    if sub in ("test", "t", "tst"):
        return Match("test")

    if sub == "run" or sub == "run-script":
        if rest and rest[0] in _NPM_SCRIPTS:
            return Match("test" if rest[0] == "test" else rest[0].split(":")[0])

        return f"only these scripts may be run: {sorted(_NPM_SCRIPTS)}"

    if sub in _NPM_INSTALL:
        return Match("install", "high", frozenset({CAP_SHELL_NETWORK}))

    if sub in ("ls", "list", "outdated"):
        return Match("inspect")

    return f"npm subcommand '{sub}' is not allowed"


def _node_matcher(args: list[str]) -> Match | str | None:
    if args in (["--version"], ["-v"]):
        return Match("inspect")

    return "node can only report its version"


def _tsc_matcher(args: list[str]) -> Match | str | None:
    if args in (["--version"], ["-v"]):
        return Match("inspect")

    if "--noEmit" in args:
        return Match("typecheck")

    return "tsc must be run with --noEmit"


def _eslint_matcher(args: list[str]) -> Match | str | None:
    reason = _no_fix(args)

    if reason:
        return reason

    for arg in args:
        if arg in ("--rulesdir", "--plugin", "--resolve-plugins-relative-to"):
            return f"'{arg}' loads code and is not allowed"

    return Match("lint")


_MVN_GOALS = {"test", "verify", "compile", "test-compile", "validate", "clean"}
_MVN_FLAGS = {"-q", "-B", "--batch-mode", "-e", "-o", "--offline", "-ntp"}
_MVN_PROP = re.compile(r"^-D[\w.\-]+(=[\w.\-]*)?$")


def _mvn_matcher(args: list[str]) -> Match | str | None:
    if args in (["--version"], ["-v"]):
        return Match("inspect")

    goals = [a for a in args if not a.startswith("-")]

    if not goals:
        return "a Maven lifecycle goal is required"

    for arg in args:
        if arg.startswith("-"):
            if arg not in _MVN_FLAGS and not _MVN_PROP.match(arg):
                return f"Maven option '{arg}' is not allowed"
        elif arg not in _MVN_GOALS:
            return f"Maven goal '{arg}' is not allowed"

    return Match("test" if "test" in goals or "verify" in goals else "build")


def _cargo_matcher(args: list[str]) -> Match | str | None:
    sub = args[0] if args else ""

    if sub in ("test", "check", "clippy", "build", "doc"):
        return Match("test" if sub == "test" else "build")

    if sub == "fmt" and "--check" in args:
        return Match("lint")

    if args == ["--version"]:
        return Match("inspect")

    return "only cargo test|check|clippy|build|doc|fmt --check is allowed"


def _go_matcher(args: list[str]) -> Match | str | None:
    sub = args[0] if args else ""

    if sub in ("test", "vet", "build"):
        return Match("test" if sub == "test" else "build")

    if args == ["version"]:
        return Match("inspect")

    return "only go test|vet|build is allowed"


# ---------------------------------------------------------- path arguments

_PATHLIKE = re.compile(r"^(\.{1,2}([/\\]|$)|[A-Za-z]:|[/\\]|~)")


def _looks_like_path(value: str) -> bool:
    return bool(_PATHLIKE.match(value)) or "/" in value or "\\" in value


def check_path_arguments(
    args: list[str], cwd: Path, workspace: Path
) -> str | None:
    """Best-effort: every path-like argument must resolve inside the workspace."""

    for arg in args:
        if "://" in arg:
            return f"URLs are not allowed in arguments: {arg[:60]!r}"

        value = arg

        if arg.startswith("-"):
            if "=" not in arg:
                continue

            value = arg.split("=", 1)[1]

        value = value.split("::", 1)[0]  # pytest node ids

        if not value or not _looks_like_path(value):
            continue

        if value.startswith("~"):
            return f"argument refers to the home directory: {arg[:60]!r}"

        try:
            resolved = (cwd / value).resolve()
        except (OSError, RuntimeError, ValueError):
            return f"unusable path argument: {arg[:60]!r}"

        if not resolved.is_relative_to(workspace):
            return f"path argument leaves the workspace: {arg[:60]!r}"

    return None


# ------------------------------------------------------------------ policy


@dataclass
class CommandPolicy:
    rules: list[CommandRule] = field(default_factory=list)
    default_timeout: float = DEFAULT_TIMEOUT_SECONDS
    max_timeout: float = MAX_TIMEOUT_SECONDS

    def clamp_timeout(self, requested: float | None) -> float:
        if requested is None:
            return self.default_timeout

        return min(max(requested, 0.1), self.max_timeout)

    def evaluate(
        self,
        argv: list[str],
        cwd: Path,
        workspace: Path,
        capabilities: frozenset[str] | set[str],
    ) -> PolicyDecision:
        def deny(reason: str, rule: str | None = None) -> PolicyDecision:
            return PolicyDecision(False, reason, rule=rule)

        if not argv:
            return deny("empty command")

        if len(argv) > MAX_ARGS or any(len(a) > MAX_ARG_CHARS for a in argv):
            return deny("command is too long")

        if any("\0" in a or "\n" in a or "\r" in a for a in argv):
            return deny("arguments must not contain control characters")

        first = argv[0]

        if "/" in first or "\\" in first or ":" in first:
            return deny("use a bare command name that is found on PATH")

        name = executable_name(first)

        if name in DENIED_EXECUTABLES:
            return deny(f"'{name}' is not allowed: {DENIED_EXECUTABLES[name]}")

        rule = self._rule_for(name)

        if rule is None:
            return deny(f"'{name}' is not on the allowed command list")

        outcome = rule.matcher(argv[1:])

        if outcome is None:
            return deny("arguments are not an allowed form", rule.name)

        if isinstance(outcome, str):
            return deny(outcome, rule.name)

        escape = check_path_arguments(argv[1:], cwd, workspace)

        if escape:
            return deny(escape, rule.name)

        missing = outcome.required_capabilities - frozenset(capabilities)

        if missing:
            return PolicyDecision(
                False,
                f"requires capability {sorted(missing)}, which has not been "
                "granted",
                rule=rule.name,
                category=outcome.category,
                risk=outcome.risk,
                required_capabilities=outcome.required_capabilities,
            )

        return PolicyDecision(
            True,
            "allowed",
            rule=rule.name,
            category=outcome.category,
            risk=outcome.risk,
            required_capabilities=outcome.required_capabilities,
        )

    def _rule_for(self, name: str) -> CommandRule | None:
        for rule in self.rules:
            if name in rule.executables or (
                rule.name == "python" and _PYTHON_NAME.match(name)
            ):
                return rule

        return None

    @classmethod
    def default(cls) -> "CommandPolicy":
        return cls(
            rules=[
                CommandRule(
                    "python", frozenset({"python", "python3", "py"}), _python_matcher
                ),
                CommandRule("pytest", frozenset({"pytest", "py.test"}), _direct_tool("pytest")),
                CommandRule("ruff", frozenset({"ruff"}), _direct_tool("ruff")),
                CommandRule("mypy", frozenset({"mypy"}), _direct_tool("mypy")),
                CommandRule("flake8", frozenset({"flake8"}), _direct_tool("flake8")),
                CommandRule("black", frozenset({"black"}), _direct_tool("black")),
                CommandRule("npm", frozenset({"npm", "pnpm", "yarn"}), _npm_matcher),
                CommandRule("node", frozenset({"node"}), _node_matcher),
                CommandRule("tsc", frozenset({"tsc"}), _tsc_matcher),
                CommandRule("eslint", frozenset({"eslint"}), _eslint_matcher),
                CommandRule("maven", frozenset({"mvn", "mvnw"}), _mvn_matcher),
                CommandRule("cargo", frozenset({"cargo"}), _cargo_matcher),
                CommandRule("go", frozenset({"go"}), _go_matcher),
            ]
        )
