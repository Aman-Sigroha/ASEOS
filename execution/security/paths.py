"""Path rules shared by every tool (no gateway dependencies).

Three questions, answered here once so all tools agree:
- Is this raw, model-supplied path even well-formed? (`raw_path_problem`)
- May it be written?                                 (`write_denied_reason`)
- May its content be shown to the model?             (`read_denied_reason`)

Comparisons are case-insensitive: Windows and default macOS file systems
treat `.GIT` and `.git` as the same directory.
"""

import os
from pathlib import PurePath

MANAGED_AREA = ".aseos_worktrees"

_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_WINDOWS_BAD_CHARS = set('<>"|?*')
_TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist")

# Files whose *content* is secret: never shown to the model by default.
_SECRET_NAMES = {
    ".env", ".npmrc", ".pypirc", ".netrc", "_netrc", ".git-credentials",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "secrets.json",
    "secrets.yml", "secrets.yaml", "credentials.json", "terraform.tfstate",
    "terraform.tfstate.backup", ".htpasswd", "service-account.json",
}
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".kdbx", ".tfvars")
_SECRET_DIRS = {".ssh", ".aws", ".gnupg", ".kube", ".azure", ".docker"}


def _parts(relative: str | PurePath) -> list[str]:
    text = str(relative).replace("\\", "/")
    return [p for p in text.split("/") if p not in ("", ".")]


def raw_path_problem(raw: object) -> str | None:
    """Reason a raw path string is malformed or unsafe, else None."""

    if not isinstance(raw, str):
        return "path must be a string"

    if "\0" in raw:
        return "path contains a NUL byte"

    if any(ord(c) < 32 for c in raw):
        return "path contains control characters"

    if os.name != "nt":
        return None

    if any(c in _WINDOWS_BAD_CHARS for c in raw):
        return "path contains characters that are invalid on Windows"

    # A drive letter ("C:\\x") is an absolute path and is caught later as an
    # escape; any other ':' is an NTFS alternate data stream ("file:stream").
    body = raw[2:] if len(raw) > 1 and raw[1] == ":" and raw[0].isalpha() else raw

    if ":" in body:
        return "':' is not allowed in paths on Windows (alternate data streams)"

    for part in _parts(raw):
        stem = part.rstrip(". ").split(".", 1)[0].upper()

        if stem in _WINDOWS_RESERVED:
            return f"'{part}' is a reserved device name on Windows"

    return None


def write_denied_reason(relative: str | PurePath) -> str | None:
    parts = [p.lower() for p in _parts(relative)]

    if ".git" in parts:
        return "Writing inside .git is not permitted"

    if MANAGED_AREA in parts:
        return "The managed worktree area belongs to other tasks"

    name = parts[-1] if parts else ""

    if name == ".env" or (
        name.startswith(".env.") and not name.endswith(_TEMPLATE_SUFFIXES)
    ):
        return f"Writing to protected file '{name}' is not permitted"

    return None


def is_secret_path(relative: str | PurePath) -> bool:
    parts = [p.lower() for p in _parts(relative)]

    if not parts:
        return False

    if any(part in _SECRET_DIRS for part in parts[:-1]):
        return True

    name = parts[-1]

    if name.endswith(_TEMPLATE_SUFFIXES):
        return False

    return (
        name in _SECRET_NAMES
        or name.startswith(".env.")
        or name.endswith(_SECRET_SUFFIXES)
    )


def read_denied_reason(
    relative: str | PurePath, allow_secrets: bool = False
) -> str | None:
    parts = [p.lower() for p in _parts(relative)]

    if MANAGED_AREA in parts:
        return "The managed worktree area belongs to other tasks"

    if not allow_secrets and is_secret_path(relative):
        return (
            "This looks like a secret-bearing file; its content is not "
            "shown (needs the 'fs.read_secrets' capability)"
        )

    return None
