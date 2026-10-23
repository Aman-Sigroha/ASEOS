"""Declarative verification checks.

A check says *what must be true*; the verification service decides how to
establish it with the gateway's tools. Checks are plain data so a plan, a
task spec or a model can carry them, and every field is validated before
anything runs.
"""

from typing import Annotated, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from execution.verification.globs import GlobError, compile_glob

MAX_ITEMS = 50
MAX_NEEDLE_LENGTH = 2000


def _strings(values: list[str], what: str) -> list[str]:
    if len(values) > MAX_ITEMS:
        raise ValueError(f"{what}: at most {MAX_ITEMS} entries")

    for value in values:
        if not value:
            raise ValueError(f"{what}: entries must not be empty")

        if len(value) > MAX_NEEDLE_LENGTH:
            raise ValueError(f"{what}: entries are limited to {MAX_NEEDLE_LENGTH} characters")

    return values


def _globs(values: list[str], what: str) -> list[str]:
    if len(values) > MAX_ITEMS:
        raise ValueError(f"{what}: at most {MAX_ITEMS} patterns")

    for value in values:
        try:
            compile_glob(value, ignore_case=False)
        except GlobError as exc:
            raise ValueError(f"{what}: {exc}") from exc

    return values


class _Check(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)
    """Optional label shown in the report; defaults to a description."""


class TestsCheck(_Check):
    __test__ = False

    kind: Literal["tests"] = "tests"
    framework: Literal[
        "auto", "pytest", "unittest", "npm", "maven", "gradle", "cargo", "go"
    ] = "auto"
    targets: list[str] = Field(default_factory=list)
    keyword: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)
    require_full_run: bool = False
    """When true, `targets`/`keyword` are rejected: a subset cannot stand in
    for the whole suite."""

    @model_validator(mode="after")
    def _full_run(self) -> "TestsCheck":
        if self.require_full_run and (self.targets or self.keyword):
            raise ValueError("require_full_run cannot be combined with targets or keyword")

        return self


class CommandCheck(_Check):
    """Build, lint or type-check by running one allowed command.

    The command must be of the same category (the command policy classifies
    it); a `lint` check that actually runs the test suite is an ERROR, not a
    pass.
    """

    kind: Literal["build", "lint", "typecheck"]
    command: str | list[str]
    cwd: str = "."
    timeout_seconds: float | None = Field(default=None, gt=0)

    @field_validator("command")
    @classmethod
    def _non_empty(cls, value: str | list[str]) -> str | list[str]:
        if isinstance(value, str) and not value.strip():
            raise ValueError("command must not be empty")

        if isinstance(value, list) and (not value or not all(isinstance(v, str) and v for v in value)):
            raise ValueError("command must be a non-empty list of non-empty strings")

        return value


class FileExistsCheck(_Check):
    kind: Literal["file_exists"] = "file_exists"
    path: str = Field(min_length=1)
    type: Literal["file", "dir", "any"] = "any"
    must_exist: bool = True
    """False asserts that the path is absent."""


class FileContentCheck(_Check):
    kind: Literal["file_content"] = "file_content"
    path: str = Field(min_length=1)
    contains: list[str] = Field(default_factory=list)
    not_contains: list[str] = Field(default_factory=list)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")

    @field_validator("contains")
    @classmethod
    def _contains(cls, value: list[str]) -> list[str]:
        return _strings(value, "contains")

    @field_validator("not_contains")
    @classmethod
    def _not_contains(cls, value: list[str]) -> list[str]:
        return _strings(value, "not_contains")

    @model_validator(mode="after")
    def _has_condition(self) -> "FileContentCheck":
        if not (self.contains or self.not_contains or self.sha256):
            raise ValueError("give at least one of contains, not_contains or sha256")

        return self


class DiffContainsCheck(_Check):
    """The change set contains (or does not contain) specific lines.

    `base` defaults to HEAD, i.e. all uncommitted work including new files.
    Use the task's base branch to include committed work as well.
    """

    kind: Literal["diff_contains"] = "diff_contains"
    base: str = "HEAD"
    paths: list[str] | None = None
    added_contains: list[str] = Field(default_factory=list)
    removed_contains: list[str] = Field(default_factory=list)
    min_files_changed: int = Field(default=0, ge=0, le=10_000)

    @field_validator("added_contains")
    @classmethod
    def _added(cls, value: list[str]) -> list[str]:
        return _strings(value, "added_contains")

    @field_validator("removed_contains")
    @classmethod
    def _removed(cls, value: list[str]) -> list[str]:
        return _strings(value, "removed_contains")

    @model_validator(mode="after")
    def _has_condition(self) -> "DiffContainsCheck":
        if not (self.added_contains or self.removed_contains or self.min_files_changed):
            raise ValueError(
                "give at least one of added_contains, removed_contains or min_files_changed"
            )

        return self


class NoProhibitedPathsCheck(_Check):
    """No changed path is prohibited (and, optionally, every changed path is
    inside an allowed set). Looks at staged, unstaged, untracked and
    conflicted paths, plus committed changes since `base` when given."""

    kind: Literal["no_prohibited_paths"] = "no_prohibited_paths"
    prohibited: list[str] = Field(default_factory=list)
    allowed_only: list[str] | None = None
    base: str | None = None

    @field_validator("prohibited")
    @classmethod
    def _prohibited(cls, value: list[str]) -> list[str]:
        return _globs(value, "prohibited")

    @field_validator("allowed_only")
    @classmethod
    def _allowed(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _globs(value, "allowed_only")

    @model_validator(mode="after")
    def _has_condition(self) -> "NoProhibitedPathsCheck":
        if not self.prohibited and self.allowed_only is None:
            raise ValueError("give prohibited and/or allowed_only")

        return self


Check = Annotated[
    Union[
        TestsCheck,
        CommandCheck,
        FileExistsCheck,
        FileContentCheck,
        DiffContainsCheck,
        NoProhibitedPathsCheck,
    ],
    Field(discriminator="kind"),
]

CHECK_KINDS = (
    "tests", "build", "lint", "typecheck", "file_exists", "file_content",
    "diff_contains", "no_prohibited_paths",
)

_ADAPTER: TypeAdapter[list[Check]] = TypeAdapter(list[Check])


def parse_checks(raw: list[dict]) -> list[Check]:
    """Validate a list of check dicts (e.g. from a plan or task spec)."""

    return _ADAPTER.validate_python(raw)


def describe(check: Check) -> str:
    """Stable human label used when the check has no explicit name."""

    if check.name:
        return check.name

    if isinstance(check, TestsCheck):
        scope = " ".join(check.targets) if check.targets else "full suite"
        return f"tests ({check.framework}): {scope}"

    if isinstance(check, CommandCheck):
        text = check.command if isinstance(check.command, str) else " ".join(check.command)
        return f"{check.kind}: {text}"

    if isinstance(check, FileExistsCheck):
        return f"{check.path} {'exists' if check.must_exist else 'is absent'}"

    if isinstance(check, FileContentCheck):
        return f"content of {check.path}"

    if isinstance(check, DiffContainsCheck):
        return "diff contains expected changes"

    return "no prohibited paths changed"
