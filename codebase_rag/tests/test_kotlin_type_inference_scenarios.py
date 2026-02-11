from __future__ import annotations

from dataclasses import dataclass, field

from codebase_rag.parsers.kotlin import KotlinTypeInferenceEngine
from codebase_rag.state.registry_cache import FunctionRegistryTrie


@dataclass
class NodeStub:
    type: str
    text: bytes | None = None
    children: list[NodeStub] = field(default_factory=list)
    fields: dict[str, NodeStub] = field(default_factory=dict)

    def child_by_field_name(self, name: str) -> NodeStub | None:
        return self.fields.get(name)


def test_kotlin_type_inference_from_annotation() -> None:
    name_node = NodeStub("identifier", text=b"user")
    type_node = NodeStub("type_identifier", text=b"User")
    declaration = NodeStub(
        "property_declaration",
        children=[name_node, type_node],
        fields={"name": name_node, "type": type_node},
    )
    root = NodeStub("source_file", children=[declaration])

    engine = KotlinTypeInferenceEngine(
        import_processor=_import_processor(),
        function_registry=FunctionRegistryTrie(),
        project_name="proj",
    )
    result = engine.build_local_variable_type_map(root, "proj.mod")

    assert result["user"] == "User"


def test_kotlin_type_inference_from_literal() -> None:
    name_node = NodeStub("identifier", text=b"title")
    value_node = NodeStub("string_literal", text=b"hello")
    declaration = NodeStub(
        "property_declaration",
        children=[name_node, value_node],
        fields={"name": name_node, "initializer": value_node},
    )
    root = NodeStub("source_file", children=[declaration])

    engine = KotlinTypeInferenceEngine(
        import_processor=_import_processor(),
        function_registry=FunctionRegistryTrie(),
        project_name="proj",
    )
    result = engine.build_local_variable_type_map(root, "proj.mod")

    assert result["title"] == "String"


def _import_processor():
    class ImportProcessorStub:
        import_mapping: dict[str, dict[str, str]] = {}

    return ImportProcessorStub()
