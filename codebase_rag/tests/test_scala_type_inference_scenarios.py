from __future__ import annotations

from dataclasses import dataclass, field

from codebase_rag.parsers.scala import ScalaTypeInferenceEngine
from codebase_rag.state.registry_cache import FunctionRegistryTrie


@dataclass
class NodeStub:
    type: str
    text: bytes | None = None
    children: list[NodeStub] = field(default_factory=list)
    fields: dict[str, NodeStub] = field(default_factory=dict)

    def child_by_field_name(self, name: str) -> NodeStub | None:
        return self.fields.get(name)


def test_scala_type_inference_from_annotations() -> None:
    name = NodeStub("identifier", text=b"user")
    type_node = NodeStub("type_identifier", text=b"User")
    definition = NodeStub(
        "val_definition",
        children=[name, type_node],
        fields={"pattern": name, "type": type_node},
    )
    root = NodeStub("compilation_unit", children=[definition])

    engine = ScalaTypeInferenceEngine(
        import_processor=_import_processor(),
        function_registry=FunctionRegistryTrie(),
        project_name="proj",
    )
    result = engine.build_local_variable_type_map(root, "proj.mod")

    assert result["user"] == "User"


def test_scala_type_inference_from_literals() -> None:
    name = NodeStub("identifier", text=b"title")
    value = NodeStub("string", text=b"hello")
    definition = NodeStub(
        "val_definition",
        children=[name, value],
        fields={"pattern": name, "value": value},
    )
    root = NodeStub("compilation_unit", children=[definition])

    engine = ScalaTypeInferenceEngine(
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
