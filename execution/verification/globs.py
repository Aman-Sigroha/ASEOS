"""Small, bounded glob matcher for repository paths.

Rules (documented in docs/EXECUTION_CONTRACT.md §2.8):
- paths use `/`; patterns are relative to the workspace root;
- `*` matches within one path segment, `?` one character, `**` any number of
  segments;
- a pattern without `/` matches that name at any depth (`.env`, `*.pem`);
- a pattern ending in `/` means everything under that directory.

Patterns are translated to anchored regular expressions built only from a
fixed set of constructs, with a cap on `**`, so a model-supplied pattern
cannot cause catastrophic backtracking.
"""

import re
from collections.abc import Iterable

MAX_PATTERN_LENGTH = 200
MAX_DOUBLE_STARS = 3


class GlobError(ValueError):
    pass


def _translate(pattern: str) -> str:
    out: list[str] = []
    i = 0

    while i < len(pattern):
        char = pattern[i]

        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("/**", i) and i + 3 == len(pattern):
            out.append("(?:/.*)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif char == "*":
            out.append("[^/]*")
            i += 1
        elif char == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(char))
            i += 1

    return "".join(out)


def compile_glob(pattern: str, *, ignore_case: bool) -> re.Pattern[str]:
    raw = pattern.strip().replace("\\", "/")

    if not raw:
        raise GlobError("empty glob pattern")

    if len(raw) > MAX_PATTERN_LENGTH:
        raise GlobError(f"glob pattern longer than {MAX_PATTERN_LENGTH} characters")

    if raw.count("**") > MAX_DOUBLE_STARS:
        raise GlobError(f"glob pattern uses '**' more than {MAX_DOUBLE_STARS} times")

    if raw.startswith("/") or ".." in raw.split("/"):
        raise GlobError("glob patterns must be relative and must not contain '..'")

    if raw.startswith("./"):
        raw = raw[2:]

    if raw.endswith("/"):
        raw += "**"

    body = _translate(raw)

    if "/" not in raw:
        body = f"(?:.*/)?{body}"  # bare names match at any depth

    flags = re.IGNORECASE if ignore_case else 0
    return re.compile(f"^{body}$", flags)


class GlobSet:
    def __init__(self, patterns: Iterable[str], *, ignore_case: bool) -> None:
        self.patterns = list(patterns)
        self._compiled = [
            (pattern, compile_glob(pattern, ignore_case=ignore_case))
            for pattern in self.patterns
        ]

    def match(self, path: str) -> str | None:
        """The first pattern that matches `path`, or None."""

        normalized = path.replace("\\", "/")

        if normalized.startswith("./"):
            normalized = normalized[2:]

        for pattern, regex in self._compiled:
            if regex.match(normalized):
                return pattern

        return None
