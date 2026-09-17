from runtime.schemas.repository import (
    FileInfo,
    RepositoryContext,
    SearchResult,
    SymbolInfo,
)


def test_file_info():
    file = FileInfo(
        path="src/auth.py",
        language="python",
        size=1200,
    )

    assert file.path == "src/auth.py"
    assert file.language == "python"
    assert file.size == 1200


def test_symbol_info():
    symbol = SymbolInfo(
        name="login",
        type="function",
        file="src/auth.py",
        line_start=10,
        line_end=30,
    )

    assert symbol.name == "login"
    assert symbol.type == "function"


def test_search_result():
    result = SearchResult(
        file="src/auth.py",
        line=42,
        text="timeout = 5000",
    )

    assert result.file == "src/auth.py"
    assert result.line == 42


def test_repository_context():
    context = RepositoryContext(
        root="C:/projects/app",
        files=[
            FileInfo(
                path="src/auth.py",
                language="python",
                size=1200,
            )
        ],
        symbols=[
            SymbolInfo(
                name="login",
                type="function",
                file="src/auth.py",
                line_start=10,
                line_end=30,
            )
        ],
        search_results=[
            SearchResult(
                file="src/auth.py",
                line=42,
                text="timeout = 5000",
            )
        ],
        summary="Python authentication project",
    )

    assert len(context.files) == 1
    assert len(context.symbols) == 1
    assert len(context.search_results) == 1
