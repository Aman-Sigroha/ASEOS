"""Parser for unified diffs (plain `diff -u` and `git diff` output).

Supported: modified, created and deleted text files, several files per
patch, `\\ No newline at end of file` markers. Rejected with a clear error:
renames/copies, mode-only changes, binary patches, quoted paths.
"""

import re
from dataclasses import dataclass, field


class PatchParseError(ValueError):
    """The patch text is not a supported, well-formed unified diff."""


@dataclass
class HunkLine:
    op: str  # " ", "-" or "+"
    text: str
    no_newline: bool = False


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[HunkLine] = field(default_factory=list)


@dataclass
class FilePatch:
    old_path: str | None  # None => file is created
    new_path: str | None  # None => file is deleted
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""

    @property
    def is_create(self) -> bool:
        return self.old_path is None

    @property
    def is_delete(self) -> bool:
        return self.new_path is None


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_UNSUPPORTED_PREFIXES = (
    ("rename from ", "renames are not supported"),
    ("rename to ", "renames are not supported"),
    ("copy from ", "copies are not supported"),
    ("copy to ", "copies are not supported"),
    ("old mode ", "mode changes are not supported"),
    ("new mode ", "mode changes are not supported"),
    ("Binary files ", "binary patches are not supported"),
    ("GIT binary patch", "binary patches are not supported"),
)


def _strip_code_fence(text: str) -> str:
    lines = text.split("\n")
    start = 0

    while start < len(lines) and lines[start].strip() == "":
        start += 1

    if start < len(lines) and lines[start].lstrip().startswith("```"):
        end = len(lines)

        while end > start and lines[end - 1].strip() == "":
            end -= 1

        if end - 1 > start and lines[end - 1].strip().startswith("```"):
            end -= 1

        return "\n".join(lines[start + 1 : end])

    return text


def _parse_path(raw: str, strip_prefix: str | None) -> str | None:
    raw = raw.split("\t", 1)[0].rstrip()

    if raw.startswith('"'):
        raise PatchParseError("quoted paths are not supported")

    if raw == "/dev/null":
        return None

    if strip_prefix and raw.startswith(strip_prefix):
        raw = raw[len(strip_prefix):]

    if not raw:
        raise PatchParseError("empty file path in patch header")

    return raw


def parse_unified_diff(text: str) -> list[FilePatch]:
    lines = [
        line[:-1] if line.endswith("\r") else line
        for line in _strip_code_fence(text).split("\n")
    ]
    files: list[FilePatch] = []
    i = 0
    git_header = False

    while i < len(lines):
        line = lines[i]

        if line.startswith("diff --git "):
            git_header = True
            i += 1
            continue

        for prefix, message in _UNSUPPORTED_PREFIXES:
            if line.startswith(prefix):
                raise PatchParseError(message)

        if line.startswith("--- ") and i + 1 < len(lines) and lines[
            i + 1
        ].startswith("+++ "):
            old_raw, new_raw = line[4:], lines[i + 1][4:]
            old_clean = old_raw.split("\t", 1)[0].rstrip()
            new_clean = new_raw.split("\t", 1)[0].rstrip()
            prefixed = old_clean.startswith("a/") or old_clean == "/dev/null"
            prefixed = prefixed and (
                new_clean.startswith("b/") or new_clean == "/dev/null"
            )
            use_prefix = git_header or prefixed
            old_path = _parse_path(old_raw, "a/" if use_prefix else None)
            new_path = _parse_path(new_raw, "b/" if use_prefix else None)

            if old_path is None and new_path is None:
                raise PatchParseError("patch has no file path")

            if (
                old_path is not None
                and new_path is not None
                and old_path != new_path
            ):
                raise PatchParseError("renames are not supported")

            file_patch = FilePatch(old_path=old_path, new_path=new_path)
            i = _parse_hunks(lines, i + 2, file_patch)
            files.append(file_patch)
            git_header = False
            continue

        if line.strip() == "" or _is_git_extended_header(line):
            i += 1
            continue

        raise PatchParseError(
            f"unexpected content outside a hunk at line {i + 1}: {line[:60]!r}"
        )

    if not files:
        raise PatchParseError("no file changes found in patch")

    for file_patch in files:
        _validate_file_patch(file_patch)

    return files


def _is_git_extended_header(line: str) -> bool:
    return line.startswith(
        (
            "index ",
            "new file mode",
            "deleted file mode",
            "similarity index",
            "dissimilarity index",
        )
    )


def _parse_hunks(lines: list[str], i: int, file_patch: FilePatch) -> int:
    while i < len(lines):
        match = _HUNK_RE.match(lines[i])

        if match is None:
            break

        old_start = int(match.group(1))
        old_count = 1 if match.group(2) is None else int(match.group(2))
        new_start = int(match.group(3))
        new_count = 1 if match.group(4) is None else int(match.group(4))
        hunk = Hunk(old_start, old_count, new_start, new_count)
        i += 1
        seen_old = seen_new = 0

        while seen_old < old_count or seen_new < new_count:
            if i >= len(lines):
                missing_old = old_count - seen_old
                missing_new = new_count - seen_new

                if missing_old == missing_new:
                    # Trailing blank context lines are often trimmed by
                    # editors; padding is safe because the context is still
                    # matched against the file.
                    hunk.lines.extend(
                        HunkLine(" ", "") for _ in range(missing_old)
                    )
                    break

                raise PatchParseError(
                    f"hunk for {file_patch.path} ends early: header says "
                    f"-{old_count} +{new_count} lines"
                )

            raw = lines[i]
            op = raw[:1]

            if raw.startswith("\\"):  # "\ No newline at end of file"
                if hunk.lines:
                    hunk.lines[-1].no_newline = True

                i += 1
                continue

            if raw == "":
                op, body = " ", ""  # editors often strip a context blank
            elif op in (" ", "-", "+"):
                body = raw[1:]
            else:
                raise PatchParseError(
                    f"unexpected line in hunk for {file_patch.path}: "
                    f"{raw[:60]!r}"
                )

            if op in (" ", "-"):
                seen_old += 1

            if op in (" ", "+"):
                seen_new += 1

            if seen_old > old_count or seen_new > new_count:
                raise PatchParseError(
                    f"hunk for {file_patch.path} has more lines than its "
                    "header declares"
                )

            hunk.lines.append(HunkLine(op, body))
            i += 1

        while i < len(lines) and lines[i].startswith("\\"):
            if hunk.lines:
                hunk.lines[-1].no_newline = True

            i += 1

        file_patch.hunks.append(hunk)

    return i


def _validate_file_patch(file_patch: FilePatch) -> None:
    if not file_patch.hunks:
        raise PatchParseError(f"no hunks for {file_patch.path}")

    ops = {line.op for hunk in file_patch.hunks for line in hunk.lines}

    if file_patch.is_create and ops != {"+"}:
        raise PatchParseError(
            f"new file {file_patch.path} may only contain added lines"
        )

    if file_patch.is_delete and ops != {"-"}:
        raise PatchParseError(
            f"deleted file {file_patch.path} may only contain removed lines"
        )

    previous_end = 0

    for hunk in file_patch.hunks:
        if hunk.old_start < previous_end:
            raise PatchParseError(
                f"hunks for {file_patch.path} overlap or are out of order"
            )

        previous_end = hunk.old_start + hunk.old_count


def reverse_patch(files: list[FilePatch]) -> list[FilePatch]:
    """Return the inverse patch (what undoes `files`)."""

    flip = {" ": " ", "+": "-", "-": "+"}
    reversed_files = []

    for file_patch in files:
        hunks = [
            Hunk(
                old_start=hunk.new_start,
                old_count=hunk.new_count,
                new_start=hunk.old_start,
                new_count=hunk.old_count,
                lines=[
                    HunkLine(flip[line.op], line.text, line.no_newline)
                    for line in hunk.lines
                ],
            )
            for hunk in file_patch.hunks
        ]
        reversed_files.append(
            FilePatch(
                old_path=file_patch.new_path,
                new_path=file_patch.old_path,
                hunks=hunks,
            )
        )

    return reversed_files
