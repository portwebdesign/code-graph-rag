from __future__ import annotations

from dataclasses import dataclass, field

from codebase_rag.parsers.go import GoTypeInferenceEngine
from codebase_rag.state.registry_cache import FunctionRegistryTrie


@dataclass
class NodeStub:
    type: str
    text: bytes | None = None
    children: list[NodeStub] = field(default_factory=list)
    fields: dict[str, NodeStub] = field(default_factory=dict)

    def child_by_field_name(self, name: str) -> NodeStub | None:
        return self.fields.get(name)


def test_go_type_inference_from_params_and_literals() -> None:
    param_name = NodeStub("identifier", text=b"arg")
    param_type = NodeStub("identifier", text=b"string")
    param = NodeStub(
        "parameter_declaration",
        children=[param_name, param_type],
        fields={"name": param_name, "type": param_type},
    )
    params = NodeStub("parameters", children=[param])
    func = NodeStub(
        "function_declaration",
        children=[params],
        fields={"parameters": params},
    )

    left = NodeStub("identifier", text=b"count")
    right = NodeStub("int_literal", text=b"1")
    assignment = NodeStub(
        "short_var_declaration",
        children=[left, right],
        fields={"left": left, "right": right},
    )

    root = NodeStub("source_file", children=[func, assignment])

    engine = GoTypeInferenceEngine(
        import_processor=_import_processor(),
        function_registry=FunctionRegistryTrie(),
        project_name="proj",
    )
    result = engine.build_local_variable_type_map(root, "proj.mod")

    assert result["arg"] == "string"
    assert result["count"] == "int"


def test_go_type_inference_from_make_call() -> None:
    make_name = NodeStub("identifier", text=b"make")
    arg = NodeStub("identifier", text=b"[]int")
    args = NodeStub("arguments", children=[arg])
    call = NodeStub(
        "call_expression",
        children=[make_name, args],
        fields={"function": make_name, "arguments": args},
    )
    left = NodeStub("identifier", text=b"items")
    assignment = NodeStub(
        "short_var_declaration",
        children=[left, call],
        fields={"left": left, "right": call},
    )

    root = NodeStub("source_file", children=[assignment])

    engine = GoTypeInferenceEngine(
        import_processor=_import_processor(),
        function_registry=FunctionRegistryTrie(),
        project_name="proj",
    )
    result = engine.build_local_variable_type_map(root, "proj.mod")

    assert result["items"] == "[]int"


def _import_processor():
    class ImportProcessorStub:
        import_mapping: dict[str, dict[str, str]] = {}

    return ImportProcessorStub()
