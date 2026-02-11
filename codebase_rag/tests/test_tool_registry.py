from codebase_rag.architecture.registry import ToolRegistry
from codebase_rag.data_models.models import ToolMetadata
from codebase_rag.data_models.types_defs import MCPInputSchema


@ToolRegistry.register("test_tool", category="test")
def _register_test_tool(_registry) -> ToolMetadata:
    return ToolMetadata(
        name="test_tool",
        description="test",
        input_schema=MCPInputSchema(type="object", properties={}, required=[]),
        handler=lambda: "ok",
        returns_json=False,
    )


def test_tool_registry_builds_tools() -> None:
    tools = ToolRegistry.build(object())
    assert "test_tool" in tools
