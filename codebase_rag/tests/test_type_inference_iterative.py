from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Protocol, cast
from unittest.mock import MagicMock

import pytest

from codebase_rag.core import constants as cs
from codebase_rag.parsers import TypeInferenceEngine
from codebase_rag.parsers.java.type_inference import JavaTypeInferenceEngine
from codebase_rag.parsers.js_ts import JsTypeInferenceEngine
from codebase_rag.parsers.lua.type_inference import LuaTypeInferenceEngine
from codebase_rag.parsers.py import PythonTypeInferenceEngine


class _LegacyTypeInferenceEngine(Protocol):
    python_type_inference: PythonTypeInferenceEngine
    java_type_inference: JavaTypeInferenceEngine
    lua_type_inference: LuaTypeInferenceEngine
    js_type_inference: JsTypeInferenceEngine
    _python_type_inference: PythonTypeInferenceEngine | None
    _java_type_inference: JavaTypeInferenceEngine | None
    _lua_type_inference: LuaTypeInferenceEngine | None
    _js_type_inference: JsTypeInferenceEngine | None

    def build_local_variable_type_map(
        self, node: Any, module_qn: str, language: cs.SupportedLanguage
    ) -> dict[str, str]: ...

    def _resolve_class_name(self, class_name: str, module_qn: str) -> str | None: ...

    def _build_java_variable_type_map(
        self, node: Any, module_qn: str
    ) -> dict[str, str]: ...


class NodeStub:
    """Minimal Tree-sitter node stub for testing traversal logic."""

    def __init__(
        self,
        node_type: str,
        *,
        children: list[NodeStub] | None = None,
        text: bytes | None = None,
        fields: dict[str, NodeStub] | None = None,
    ) -> None:
        self.type = node_type
        self._children = children or []
        self.text = text
        self._fields = fields or {}

    @property
    def children(self) -> list[NodeStub]:
        return list(self._children)

    def child_by_field_name(self, name: str) -> NodeStub | None:
        return self._fields.get(name)


def _build_deep_assignment_chain(depth: int) -> NodeStub:
    """Create a deeply nested assignment chain exceeding recursion limits."""

    next_node: NodeStub | None = None
    for index in range(depth):
        attr = NodeStub(
            "attribute",
            text=f"self.attr{index}".encode(),
        )
        right = NodeStub("identifier", text=b"value")
        current = NodeStub(
            "assignment",
            children=[attr, right] + ([next_node] if next_node else []),
            fields={"left": attr, "right": right},
        )
        next_node = current

    return NodeStub("block", children=[next_node] if next_node else [])


def _build_deep_return_tree(depth: int) -> NodeStub:
    """Create a deeply nested tree containing many return statements."""

    current: NodeStub = NodeStub("return_statement")
    for _ in range(depth):
        return_node = NodeStub("return_statement")
        current = NodeStub("block", children=[return_node, current])

    return current


def _make_engine() -> _LegacyTypeInferenceEngine:
    engine = TypeInferenceEngine(
        import_processor=MagicMock(),
        function_registry=MagicMock(),
        simple_name_lookup=defaultdict(set),
        repo_path=Path("."),
        project_name="proj",
        ast_cache=MagicMock(),
        queries={},
        module_qn_to_file_path={},
        class_inheritance={},
    )
    return cast(_LegacyTypeInferenceEngine, engine)


def test_analyze_self_assignments_handles_deep_tree_without_recursion_error() -> None:
    engine = _make_engine()
    py_engine = engine.python_type_inference

    mock_infer = MagicMock(return_value="MockType")
    py_engine._infer_type_from_expression = cast(Any, mock_infer)

    root = _build_deep_assignment_chain(depth=1500)
    local_types: dict[str, str] = {}

    py_engine._analyze_self_assignments(cast(Any, root), local_types, "proj.module")

    assert local_types, "Expected at least one inferred instance variable"
    assert mock_infer.call_count == 1500


def test_find_return_statements_handles_deep_tree_without_recursion_error() -> None:
    engine = _make_engine()
    py_engine = engine.python_type_inference

    root = _build_deep_return_tree(depth=1500)
    returns: list[Any] = []

    py_engine._find_return_statements(cast(Any, root), returns)

    assert len(returns) == 1501


class TestLazyPropertyInitialization:
    def test_python_type_inference_lazy_init(self) -> None:
        engine = _make_engine()

        assert engine._python_type_inference is None
        result = engine.python_type_inference
        assert isinstance(result, PythonTypeInferenceEngine)
        assert engine._python_type_inference is result
        assert engine.python_type_inference is result

    def test_java_type_inference_lazy_init(self) -> None:
        engine = _make_engine()

        assert engine._java_type_inference is None
        result = engine.java_type_inference
        assert isinstance(result, JavaTypeInferenceEngine)
        assert engine._java_type_inference is result
        assert engine.java_type_inference is result

    def test_lua_type_inference_lazy_init(self) -> None:
        engine = _make_engine()

        assert engine._lua_type_inference is None
        result = engine.lua_type_inference
        assert isinstance(result, LuaTypeInferenceEngine)
        assert engine._lua_type_inference is result
        assert engine.lua_type_inference is result

    def test_js_type_inference_lazy_init(self) -> None:
        engine = _make_engine()

        assert engine._js_type_inference is None
        result = engine.js_type_inference
        assert isinstance(result, JsTypeInferenceEngine)
        assert engine._js_type_inference is result
        assert engine.js_type_inference is result


class TestBuildLocalVariableTypeMapDispatch:
    @pytest.fixture
    def engine(self) -> _LegacyTypeInferenceEngine:
        return _make_engine()

    @pytest.fixture
    def mock_node(self) -> MagicMock:
        return MagicMock()

    def test_dispatches_to_python_engine(
        self, engine: _LegacyTypeInferenceEngine, mock_node: MagicMock
    ) -> None:
        expected = {"var1": "str"}
        mock_build = MagicMock(return_value=expected)
        engine.python_type_inference.build_local_variable_type_map = cast(
            Any, mock_build
        )

        result = engine.build_local_variable_type_map(
            mock_node, "proj.module", cs.SupportedLanguage.PYTHON
        )

        assert result == expected
        mock_build.assert_called_once_with(mock_node, "proj.module")

    def test_dispatches_to_js_engine(
        self, engine: _LegacyTypeInferenceEngine, mock_node: MagicMock
    ) -> None:
        expected = {"jsVar": "number"}
        mock_build = MagicMock(return_value=expected)
        engine.js_type_inference.build_local_variable_type_map = cast(Any, mock_build)

        result = engine.build_local_variable_type_map(
            mock_node, "proj.module", cs.SupportedLanguage.JS
        )

        assert result == expected
        mock_build.assert_called_once_with(mock_node, "proj.module")

    def test_dispatches_to_ts_engine(
        self, engine: _LegacyTypeInferenceEngine, mock_node: MagicMock
    ) -> None:
        expected = {"tsVar": "string"}
        mock_build = MagicMock(return_value=expected)
        engine.js_type_inference.build_local_variable_type_map = cast(Any, mock_build)

        result = engine.build_local_variable_type_map(
            mock_node, "proj.module", cs.SupportedLanguage.TS
        )

        assert result == expected
        mock_build.assert_called_once_with(mock_node, "proj.module")

    def test_dispatches_to_java_engine(
        self, engine: _LegacyTypeInferenceEngine, mock_node: MagicMock
    ) -> None:
        expected = {"javaVar": "String"}
        mock_build = MagicMock(return_value=expected)
        engine.java_type_inference.build_variable_type_map = cast(Any, mock_build)

        result = engine.build_local_variable_type_map(
            mock_node, "proj.module", cs.SupportedLanguage.JAVA
        )

        assert result == expected
        mock_build.assert_called_once_with(mock_node, "proj.module")

    def test_dispatches_to_lua_engine(
        self, engine: _LegacyTypeInferenceEngine, mock_node: MagicMock
    ) -> None:
        expected = {"luaVar": "table"}
        mock_build = MagicMock(return_value=expected)
        engine.lua_type_inference.build_local_variable_type_map = cast(Any, mock_build)

        result = engine.build_local_variable_type_map(
            mock_node, "proj.module", cs.SupportedLanguage.LUA
        )

        assert result == expected
        mock_build.assert_called_once_with(mock_node, "proj.module")

    @pytest.mark.parametrize(
        "language",
        [
            cs.SupportedLanguage.RUST,
            cs.SupportedLanguage.GO,
            cs.SupportedLanguage.SCALA,
            cs.SupportedLanguage.CPP,
            cs.SupportedLanguage.CSHARP,
            cs.SupportedLanguage.PHP,
        ],
    )
    def test_returns_empty_dict_for_unsupported_language(
        self,
        engine: _LegacyTypeInferenceEngine,
        mock_node: MagicMock,
        language: cs.SupportedLanguage,
    ) -> None:
        result = engine.build_local_variable_type_map(
            mock_node, "proj.module", language
        )

        assert result == {}


class TestResolveClassName:
    def test_delegates_to_resolve_class_name_function(self) -> None:
        mock_import_processor = MagicMock()
        mock_import_processor.import_mapping = {
            "proj.module": {"MyClass": "proj.models.MyClass"}
        }
        mock_function_registry = MagicMock()
        mock_function_registry.__contains__ = MagicMock(return_value=False)

        engine = TypeInferenceEngine(
            import_processor=mock_import_processor,
            function_registry=mock_function_registry,
            simple_name_lookup=defaultdict(set),
            repo_path=Path("."),
            project_name="proj",
            ast_cache=MagicMock(),
            queries={},
            module_qn_to_file_path={},
            class_inheritance={},
        )

        result = cast(Any, engine)._resolve_class_name("MyClass", "proj.module")

        assert result == "proj.models.MyClass"

    def test_returns_none_when_class_not_found(self) -> None:
        mock_import_processor = MagicMock()
        mock_import_processor.import_mapping = {}
        mock_function_registry = MagicMock()
        mock_function_registry.__contains__ = MagicMock(return_value=False)

        engine = TypeInferenceEngine(
            import_processor=mock_import_processor,
            function_registry=mock_function_registry,
            simple_name_lookup=defaultdict(set),
            repo_path=Path("."),
            project_name="proj",
            ast_cache=MagicMock(),
            queries={},
            module_qn_to_file_path={},
            class_inheritance={},
        )

        result = cast(Any, engine)._resolve_class_name("UnknownClass", "proj.module")

        assert result is None


class TestBuildJavaVariableTypeMap:
    def test_delegates_to_java_engine(self) -> None:
        engine = _make_engine()
        mock_node = MagicMock()
        expected = {"javaVar": "String", "count": "int"}
        mock_build = MagicMock(return_value=expected)
        engine.java_type_inference.build_variable_type_map = cast(Any, mock_build)

        result = engine._build_java_variable_type_map(mock_node, "com.example.Module")

        assert result == expected
        mock_build.assert_called_once_with(mock_node, "com.example.Module")
