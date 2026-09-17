from pydantic import BaseModel, Field


class FileInfo(BaseModel):
    """Metadata about a repository file."""

    path: str = Field(min_length=1)
    language: str | None = None
    size: int | None = Field(default=None, ge=0)


class SymbolInfo(BaseModel):
    """A symbol discovered inside source code."""

    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    file: str = Field(min_length=1)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)


class SearchResult(BaseModel):
    """A single repository search match."""

    file: str = Field(min_length=1)
    line: int = Field(ge=1)
    text: str


class RepositoryContext(BaseModel):
    """Structured context about a software repository."""

    root: str = Field(min_length=1)
    files: list[FileInfo] = Field(default_factory=list)
    symbols: list[SymbolInfo] = Field(default_factory=list)
    search_results: list[SearchResult] = Field(default_factory=list)
    summary: str = ""
