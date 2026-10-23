"""Secret redaction for command output and stored artifacts.

Two layers:
1. exact values of secret-looking variables in the parent environment;
2. well-known credential shapes (private keys, cloud/API tokens, JWTs,
   bearer headers, URL credentials, `password=...` assignments).

Redaction is best-effort defense in depth, not a guarantee: an arbitrary
secret with no recognizable shape and no environment entry cannot be found.
Every pattern uses bounded quantifiers and no nested repetition so hostile
output cannot make it backtrack catastrophically.
"""

import os
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field

from execution.commands.env import SECRET_NAME

MASK = "[REDACTED]"
MIN_ENV_SECRET_LENGTH = 8

_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY-----"
    r"(?:[\s\S]{0,20000}?-----END [A-Z0-9 ]{0,40}PRIVATE KEY-----|[\s\S]{0,4000})"
)
# Specific names only: bare "pass" or "auth" would mask "passed: 5" or "author: x".
_SECRET_NAMES = (
    r"(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|"
    r"authorization|credentials?)"
)

# (kind, pattern, replacement). `\1`-style groups keep the key, mask the value.
PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("private_key", _PRIVATE_KEY, MASK),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), MASK),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,255}|github_pat_[A-Za-z0-9_]{40,255})\b"), MASK),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,200}\b"), MASK),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,200}\b"), MASK),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), MASK),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,2000}\.eyJ[A-Za-z0-9_-]{8,4000}\.[A-Za-z0-9_-]{0,2000}"),
        MASK,
    ),
    (
        "bearer",
        re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,2000}"),
        r"\1 " + MASK,
    ),
    (
        "url_credentials",
        re.compile(r"\b([a-z][a-z0-9+.-]{1,20}://)[^\s/:@]{1,200}:[^\s/@]{1,200}@"),
        r"\1" + MASK + "@",
    ),
    (
        "assignment",
        re.compile(
            r"(?i)\b([A-Za-z0-9_.-]{0,40}" + _SECRET_NAMES + r"[A-Za-z0-9_.-]{0,20})"
            r"(\s{0,3}[=:]\s{0,3})(?:\"[^\"\r\n]{1,500}\"|'[^'\r\n]{1,500}'|[^\s\"',;&]{1,500})"
        ),
        r"\1\2" + MASK,
    ),
)


@dataclass
class Redaction:
    text: str
    count: int = 0
    kinds: dict[str, int] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return self.count > 0


def _env_secrets(env: Mapping[str, str] | None) -> list[str]:
    parent = os.environ if env is None else env
    values = {
        value
        for name, value in parent.items()
        if SECRET_NAME.search(name) and len(value) >= MIN_ENV_SECRET_LENGTH
    }
    return sorted(values, key=len, reverse=True)  # longest first


def explicit_secrets(env: Mapping[str, object] | None) -> tuple[str, ...]:
    """Secret-looking values among variables a caller passed to ONE command
    (they are not in this process's environment, so they would otherwise go
    unmasked when the command prints them)."""

    if not isinstance(env, Mapping):
        return ()

    return tuple(
        value
        for name, value in env.items()
        if isinstance(value, str)
        and SECRET_NAME.search(str(name))
        and len(value) >= MIN_ENV_SECRET_LENGTH
    )


def redact_text(
    text: str,
    env: Mapping[str, str] | None = None,
    extra: tuple[str, ...] = (),
) -> Redaction:
    """Mask secrets in `text`; report how many and which kinds. `extra` are
    further literal values to mask (see `explicit_secrets`)."""

    if not text:
        return Redaction(text)

    counts: Counter[str] = Counter()

    for value in sorted({*_env_secrets(env), *extra}, key=len, reverse=True):
        hits = text.count(value)

        if hits:
            text = text.replace(value, MASK)
            counts["environment_value"] += hits

    for kind, pattern, replacement in PATTERNS:
        text, hits = pattern.subn(replacement, text)

        if hits:
            counts[kind] += hits

    return Redaction(text, sum(counts.values()), dict(counts))
