from __future__ import annotations

from dataclasses import dataclass, field

from codebase_rag.parsers.php import PhpTypeInferenceEngine
from codebase_rag.state.registry_cache import FunctionRegistryTrie


@dataclass
class NodeStub:
    type: str
    text: bytes | None = None
    children: list[NodeStub] = field(default_factory=list)
    fields: dict[str, NodeStub] = field(default_factory=dict)

    def child_by_field_name(self, name: str) -> NodeStub | None:
        return self.fields.get(name)


def test_php_type_inference_from_params() -> None:
    param_name = NodeStub("variable_name", text=b"$user")
    param_type = NodeStub("type_identifier", text=b"User")
    param = NodeStub(
        "simple_parameter",
        children=[param_name, param_type],
        fields={"name": param_name, "type": param_type},
    )
    params = NodeStub("parameters", children=[param])
    func = NodeStub(
        "function_definition",
        children=[params],
        fields={"parameters": params},
    )
    root = NodeStub("program", children=[func])

    engine = PhpTypeInferenceEngine(
        import_processor=_import_processor(),
        function_registry=FunctionRegistryTrie(),
        project_name="proj",
    )
    result = engine.build_local_variable_type_map(root, "proj.mod")

    assert result["user"] == "User"


def test_php_type_inference_from_assignment_and_new() -> None:
    left = NodeStub("variable_name", text=b"$name")
    right = NodeStub("string", text=b"hello")
    assignment = NodeStub(
        "assignment_expression",
        children=[left, right],
        fields={"left": left, "right": right},
    )

    class_node = NodeStub("name", text=b"Order")
    new_expr = NodeStub(
        "new_expression",
        children=[class_node],
        fields={"class": class_node},
    )
    left_order = NodeStub("variable_name", text=b"$order")
    assignment_order = NodeStub(
        "assignment_expression",
        children=[left_order, new_expr],
        fields={"left": left_order, "right": new_expr},
    )

    root = NodeStub("program", children=[assignment, assignment_order])

    engine = PhpTypeInferenceEngine(
        import_processor=_import_processor(),
        function_registry=FunctionRegistryTrie(),
        project_name="proj",
    )
    result = engine.build_local_variable_type_map(root, "proj.mod")

    assert result["name"] == "string"
    assert result["order"] == "Order"


def _import_processor():
    class ImportProcessorStub:
        import_mapping: dict[str, dict[str, str]] = {}

    return ImportProcessorStub()
