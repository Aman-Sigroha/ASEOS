"""Explicit environment policy for child processes.

Nothing is inherited by default. A variable reaches the child only if it
is on the base allow-list *and* does not look like a secret, or if the
caller passed it explicitly and it is on the (small) per-call allow-list.
"""

import os
import re
from collections.abc import Mapping
from pathlib import Path

# Needed for programs to start and behave on their respective platforms.
BASE_ALLOWED = frozenset(
    name.upper()
    for name in (
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "OS",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMDATA",
        "COMMONPROGRAMFILES",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "HOME",
        "LOGNAME",
        "USER",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "VIRTUAL_ENV",
        "JAVA_HOME",
        "GOPATH",
        "CARGO_HOME",
        "RUSTUP_HOME",
    )
)

# Variables a caller may set per call. Deliberately tiny: nothing that
# changes which code is loaded (PYTHONPATH, NODE_OPTIONS, LD_PRELOAD, ...).
CALLER_ALLOWED = frozenset(
    {
        "CI",
        "NODE_ENV",
        "PYTHONHASHSEED",
        "PYTHONDONTWRITEBYTECODE",
        "TZ",
        "LANG",
        "LC_ALL",
        "FORCE_COLOR",
        "NO_COLOR",
    }
)

# Applied on top of BASE_ALLOWED so a secret can never ride along by name.
SECRET_NAME = re.compile(
    r"(KEY|TOKEN|SECRET|PASSW|CREDENTIAL|AUTH|COOKIE|SESSION|PRIVATE|CERT)",
    re.IGNORECASE,
)

FORCED = {
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
    "GIT_TERMINAL_PROMPT": "0",
}

_VALID_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_VALUE_CHARS = 1024


class EnvironmentError_(ValueError):
    """A caller-supplied environment variable is not allowed."""


def validate_caller_env(extra: Mapping[str, str] | None) -> dict[str, str]:
    cleaned: dict[str, str] = {}

    for name, value in (extra or {}).items():
        if not _VALID_NAME.match(name):
            raise EnvironmentError_(f"Invalid environment variable name: {name!r}")

        if name.upper() not in CALLER_ALLOWED:
            raise EnvironmentError_(
                f"Environment variable '{name}' is not allowed; allowed: "
                f"{sorted(CALLER_ALLOWED)}"
            )

        if not isinstance(value, str) or "\0" in value or len(value) > MAX_VALUE_CHARS:
            raise EnvironmentError_(f"Invalid value for environment variable '{name}'")

        cleaned[name] = value

    return cleaned


def build_environment(
    temp_dir: Path,
    extra: Mapping[str, str] | None = None,
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return the complete environment for a child process."""

    parent = os.environ if source is None else source
    env: dict[str, str] = {}

    for name, value in parent.items():
        upper = name.upper()

        if upper in BASE_ALLOWED and not SECRET_NAME.search(name):
            env[name] = value

    env.update(FORCED)
    env["TEMP"] = env["TMP"] = env["TMPDIR"] = str(temp_dir)
    env.update(validate_caller_env(extra))
    return env


def redact(text: str, env: Mapping[str, str] | None = None) -> str:
    """Hide the values of secret-looking variables in the parent environment.

    Used on captured output so a command that echoes its own environment
    cannot leak a token into the result.
    """

    parent = os.environ if env is None else env

    for name, value in parent.items():
        if SECRET_NAME.search(name) and len(value) >= 8:
            text = text.replace(value, "[REDACTED]")

    return text
