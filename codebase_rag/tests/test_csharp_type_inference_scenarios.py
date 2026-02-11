from __future__ import annotations

from dataclasses import dataclass, field

from codebase_rag.parsers.csharp import CSharpTypeInferenceEngine
from codebase_rag.state.registry_cache import FunctionRegistryTrie


@dataclass
class NodeStub:
    type: str
    text: bytes | None = None
    children: list[NodeStub] = field(default_factory=list)
    fields: dict[str, NodeStub] = field(default_factory=dict)

    def child_by_field_name(self, name: str) -> NodeStub | None:
        return self.fields.get(name)


def test_csharp_type_inference_from_declaration() -> None:
    type_node = NodeStub("identifier", text=b"User")
    name_node = NodeStub("identifier", text=b"user")
    declarator = NodeStub(
        "variable_declarator",
        children=[name_node],
        fields={"name": name_node},
    )
    declaration = NodeStub(
        "variable_declaration",
        children=[type_node, declarator],
        fields={"type": type_node},
    )
    stmt = NodeStub(
        "local_declaration_statement",
        children=[declaration],
        fields={"declaration": declaration},
    )
    root = NodeStub("compilation_unit", children=[stmt])

    engine = CSharpTypeInferenceEngine(
        import_processor=_import_processor(),
        function_registry=FunctionRegistryTrie(),
        project_name="proj",
    )
    result = engine.build_local_variable_type_map(root, "proj.mod")

    assert result["user"] == "User"


def test_csharp_type_inference_from_var_initializer() -> None:
    type_node = NodeStub("identifier", text=b"var")
    name_node = NodeStub("identifier", text=b"widget")
    created_type = NodeStub("identifier", text=b"Widget")
    creation = NodeStub(
        "object_creation_expression",
        children=[created_type],
        fields={"type": created_type},
    )
    declarator = NodeStub(
        "variable_declarator",
        children=[name_node, creation],
        fields={"name": name_node, "value": creation},
    )
    declaration = NodeStub(
        "variable_declaration",
        children=[type_node, declarator],
        fields={"type": type_node},
    )
    stmt = NodeStub(
        "local_declaration_statement",
        children=[declaration],
        fields={"declaration": declaration},
    )
    root = NodeStub("compilation_unit", children=[stmt])

    engine = CSharpTypeInferenceEngine(
        import_processor=_import_processor(),
        function_registry=FunctionRegistryTrie(),
        project_name="proj",
    )
    result = engine.build_local_variable_type_map(root, "proj.mod")

    assert result["widget"] == "Widget"


def test_csharp_type_inference_from_assignment() -> None:
    left = NodeStub("identifier", text=b"title")
    right = NodeStub("string_literal", text=b"hello")
    assignment = NodeStub(
        "assignment_expression",
        children=[left, right],
        fields={"left": left, "right": right},
    )
    root = NodeStub("compilation_unit", children=[assignment])

    engine = CSharpTypeInferenceEngine(
        import_processor=_import_processor(),
        function_registry=FunctionRegistryTrie(),
        project_name="proj",
    )
    result = engine.build_local_variable_type_map(root, "proj.mod")

    assert result["title"] == "string"


def _import_processor():
    class ImportProcessorStub:
        import_mapping: dict[str, dict[str, str]] = {}

    return ImportProcessorStub()
