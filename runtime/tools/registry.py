from pydantic import BaseModel, Field

from runtime.schemas.action import ActionType


class ToolDefinition(BaseModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    action_type: ActionType
    enabled: bool = True


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
