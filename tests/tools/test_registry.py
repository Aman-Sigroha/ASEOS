import pytest

from runtime.tools.registry import ToolDefinition, ToolRegistry


def test_registry_registers_and_gets_tool():
    tool = ToolDefinition(
        name="search",
        description="Search repository.",
        action_type="SEARCH",
    )

    registry = ToolRegistry()
    registry.register(tool)

    assert registry.get("SEARCH") is tool
    assert registry.is_available("SEARCH") is True


def test_registry_rejects_duplicate_action_type():
    registry = ToolRegistry()

    registry.register(
        ToolDefinition(
            name="search",
            description="Search repository.",
            action_type="SEARCH",
        )
    )

    with pytest.raises(
        ValueError,
        match="already registered",
    ):
        registry.register(
            ToolDefinition(
                name="another-search",
                description="Another search tool.",
                action_type="SEARCH",
            )
        )


def test_registry_returns_none_for_unknown_tool():
    registry = ToolRegistry()

    assert registry.get("SEARCH") is None


def test_registry_require_returns_enabled_tool():
    tool = ToolDefinition(
        name="read",
        description="Read a repository file.",
        action_type="READ",
    )

    registry = ToolRegistry([tool])

    assert registry.require("READ") is tool


def test_registry_require_rejects_unknown_tool():
    registry = ToolRegistry()

    with pytest.raises(
        KeyError,
        match="No tool is registered",
    ):
        registry.require("SEARCH")


def test_registry_require_rejects_disabled_tool():
    registry = ToolRegistry(
        [
            ToolDefinition(
                name="edit",
                description="Edit a file.",
                action_type="EDIT",
                enabled=False,
            )
        ]
    )

    with pytest.raises(
        ValueError,
        match="disabled",
    ):
        registry.require("EDIT")


def test_registry_lists_only_enabled_tools():
    registry = ToolRegistry(
        [
            ToolDefinition(
                name="search",
                description="Search repository.",
                action_type="SEARCH",
            ),
            ToolDefinition(
                name="edit",
                description="Edit a file.",
                action_type="EDIT",
                enabled=False,
            ),
        ]
    )

    tools = registry.list_available()

    assert len(tools) == 1
    assert tools[0].action_type == "SEARCH"


def test_default_registry_contains_all_current_action_types():
    registry = ToolRegistry.default()

    assert {tool.action_type for tool in registry.list_available()} == {
        "SEARCH",
        "READ",
        "EDIT",
        "RUN_TEST",
        "RUN_COMMAND",
        "GIT_DIFF",
    }
