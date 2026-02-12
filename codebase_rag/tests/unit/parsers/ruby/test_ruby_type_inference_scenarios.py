from __future__ import annotations

from dataclasses import dataclass, field

from codebase_rag.parsers.languages.ruby import RubyTypeInferenceEngine
from codebase_rag.state.registry_cache import FunctionRegistryTrie


@dataclass
class NodeStub:
    type: str
    text: bytes | None = None
    children: list[NodeStub] = field(default_factory=list)
    fields: dict[str, NodeStub] = field(default_factory=dict)

    def child_by_field_name(self, name: str) -> NodeStub | None:
        return self.fields.get(name)


def test_ruby_type_inference_from_literal_and_constructor() -> None:
    left_name = NodeStub("identifier", text=b"name")
    right_name = NodeStub("string", text=b"hello")
    assignment_name = NodeStub(
        "assignment",
        children=[left_name, right_name],
        fields={"left": left_name, "right": right_name},
    )

    left_user = NodeStub("identifier", text=b"user")
    call_const = NodeStub("constant", text=b"User")
    call_node = NodeStub("call", text=b"User.new", children=[call_const])
    assignment_user = NodeStub(
        "assignment",
        children=[left_user, call_node],
        fields={"left": left_user, "right": call_node},
    )

    root = NodeStub("program", children=[assignment_name, assignment_user])

    engine = RubyTypeInferenceEngine(
        import_processor=_import_processor(),
        function_registry=FunctionRegistryTrie(),
        project_name="proj",
    )
    result = engine.build_local_variable_type_map(root, "proj.mod")

    assert result["name"] == "String"
    assert result["user"] == "User"


def _import_processor():
    class ImportProcessorStub:
        import_mapping: dict[str, dict[str, str]] = {}

    return ImportProcessorStub()
