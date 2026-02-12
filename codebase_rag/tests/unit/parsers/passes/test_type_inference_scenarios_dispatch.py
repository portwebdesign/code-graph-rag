from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Protocol, cast
from unittest.mock import MagicMock

from codebase_rag.core import constants as cs
from codebase_rag.parsers import TypeInferenceEngine


class _DispatchTypeInferenceEngine(Protocol):
    go_type_inference: Any
    scala_type_inference: Any
    csharp_type_inference: Any
    php_type_inference: Any
    ruby_type_inference: Any

    def build_local_variable_type_map(
        self, node: Any, module_qn: str, language: cs.SupportedLanguage
    ) -> dict[str, str]: ...


def _make_engine() -> _DispatchTypeInferenceEngine:
    engine = TypeInferenceEngine(
        import_processor=MagicMock(),
        function_registry=MagicMock(),
        simple_name_lookup=defaultdict(set),
        repo_path=Path("../../.."),
        project_name="proj",
        ast_cache=MagicMock(),
        queries={},
        module_qn_to_file_path={},
        class_inheritance={},
    )
    return cast(_DispatchTypeInferenceEngine, engine)


def test_dispatches_to_go_engine() -> None:
    engine = _make_engine()
    engine.go_type_inference.build_local_variable_type_map = MagicMock(
        return_value={"v": "int"}
    )
    result = engine.build_local_variable_type_map(
        MagicMock(), "proj.mod", cs.SupportedLanguage.GO
    )
    assert result == {"v": "int"}


def test_dispatches_to_scala_engine() -> None:
    engine = _make_engine()
    engine.scala_type_inference.build_local_variable_type_map = MagicMock(
        return_value={"v": "String"}
    )
    result = engine.build_local_variable_type_map(
        MagicMock(), "proj.mod", cs.SupportedLanguage.SCALA
    )
    assert result == {"v": "String"}


def test_dispatches_to_csharp_engine() -> None:
    engine = _make_engine()
    engine.csharp_type_inference.build_local_variable_type_map = MagicMock(
        return_value={"v": "User"}
    )
    result = engine.build_local_variable_type_map(
        MagicMock(), "proj.mod", cs.SupportedLanguage.CSHARP
    )
    assert result == {"v": "User"}


def test_dispatches_to_php_engine() -> None:
    engine = _make_engine()
    engine.php_type_inference.build_local_variable_type_map = MagicMock(
        return_value={"v": "string"}
    )
    result = engine.build_local_variable_type_map(
        MagicMock(), "proj.mod", cs.SupportedLanguage.PHP
    )
    assert result == {"v": "string"}


def test_dispatches_to_ruby_engine() -> None:
    engine = _make_engine()
    engine.ruby_type_inference.build_local_variable_type_map = MagicMock(
        return_value={"v": "String"}
    )
    result = engine.build_local_variable_type_map(
        MagicMock(), "proj.mod", cs.SupportedLanguage.RUBY
    )
    assert result == {"v": "String"}
