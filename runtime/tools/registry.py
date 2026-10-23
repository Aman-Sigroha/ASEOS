from pydantic import BaseModel, Field

from runtime.schemas.action import ActionType


class ToolDefinition(BaseModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    action_type: ActionType
    enabled: bool = True
    # Filled in when the registry is built from an execution gateway; the
    # hand-written defaults leave them unset.
    capabilities: list[str] = Field(default_factory=list)
    read_only: bool | None = None
    requires_approval: bool = False


class ToolRegistry:
    """Registry of capabilities that the agent is allowed to use."""

    def __init__(
        self,
        tools: list[ToolDefinition] | None = None,
    ) -> None:
        self._tools: dict[ActionType, ToolDefinition] = {}

        for tool in tools or []:
            self.register(tool)

    def register(self, tool: ToolDefinition) -> None:
        if tool.action_type in self._tools:
            raise ValueError(
                f"Tool for action type '{tool.action_type}' is already registered."
            )

        self._tools[tool.action_type] = tool

    def get(self, action_type: ActionType) -> ToolDefinition | None:
        return self._tools.get(action_type)

    def require(self, action_type: ActionType) -> ToolDefinition:
        tool = self.get(action_type)

        if tool is None:
            raise KeyError(f"No tool is registered for action type '{action_type}'.")

        if not tool.enabled:
            raise ValueError(f"Tool '{tool.name}' is disabled.")

        return tool

    def is_available(self, action_type: ActionType) -> bool:
        tool = self.get(action_type)
        return tool is not None and tool.enabled

    def list_available(self) -> list[ToolDefinition]:
        return [tool for tool in self._tools.values() if tool.enabled]

    def requiring_approval(self) -> set[str]:
        """Action types whose tool is registered as needing human approval."""

        return {
            tool.action_type
            for tool in self._tools.values()
            if tool.enabled and tool.requires_approval
        }

    @classmethod
    def from_tool_descriptions(cls, descriptions: list[dict]) -> "ToolRegistry":
        """Build a registry from an execution gateway's `describe_tools()`.

        The gateway is the source of truth for what exists, what it needs and
        whether it is available, so the registry cannot drift from it. A tool
        the gateway has not granted is registered but disabled."""

        return cls(
            tools=[
                ToolDefinition(
                    name=item["name"],
                    description=item.get("description") or item["name"],
                    action_type=item["action_type"],
                    enabled=bool(item.get("available", True)),
                    capabilities=list(item.get("capabilities", [])),
                    read_only=item.get("read_only"),
                    requires_approval=bool(item.get("requires_approval", False)),
                )
                for item in descriptions
            ]
        )

    @classmethod
    def default(cls) -> "ToolRegistry":
        return cls(
            tools=[
                ToolDefinition(
                    name="search",
                    description="Search the repository for files or text.",
                    action_type="SEARCH",
                ),
                ToolDefinition(
                    name="read",
                    description="Read a file from the repository.",
                    action_type="READ",
                ),
                ToolDefinition(
                    name="edit",
                    description="Modify an existing repository file.",
                    action_type="EDIT",
                ),
                ToolDefinition(
                    name="run_test",
                    description="Run the project's test suite or a test subset.",
                    action_type="RUN_TEST",
                ),
                ToolDefinition(
                    name="run_command",
                    description="Run an explicitly permitted development command.",
                    action_type="RUN_COMMAND",
                ),
                ToolDefinition(
                    name="git_diff",
                    description="Inspect the current Git diff.",
                    action_type="GIT_DIFF",
                ),
            ]
        )
