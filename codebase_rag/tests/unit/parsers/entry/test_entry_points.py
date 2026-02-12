from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple, cast

from tree_sitter import Node

from codebase_rag.core import constants as cs
from codebase_rag.parsers.pipeline.function_ingest import (
    FunctionIngestMixin,
    FunctionResolution,
)


class MockNode(NamedTuple):
    type: str
    text: bytes = b""
    children: list[Any] = []

    def child_by_field_name(self, name: str) -> Any | None:
        return None


class DummyIngestor(FunctionIngestMixin):
    """
    A dummy ingestor for testing entry point detection.

    This class mocks the necessary parts of the ingestor to allow testing
    the `_detect_entry_point` method mixed in from `FunctionIngestMixin`.
    """

    def __init__(self, module_map: dict[str, Path]) -> None:
        self.module_qn_to_file_path = module_map

    def _get_docstring(self, node):
        return None

    def _extract_decorators(self, node):
        return []


def test_python_main_entry_point(tmp_path: Path) -> None:
    """Test detection of a standard Python main entry point."""

    module_qn = "app.main"
    module_map = {module_qn: tmp_path / "main.py"}
    ingestor = DummyIngestor(module_map)
    resolution = FunctionResolution("app.main", "main", False)

    assert (
        ingestor._detect_entry_point(
            cast(Node, MockNode("function")),
            resolution,
            module_qn,
            cs.SupportedLanguage.PYTHON,
            [],
        )
        is True
    )


def test_python_decorated_entry_point(tmp_path: Path) -> None:
    module_qn = "api.routes"
    module_map = {module_qn: tmp_path / "routes.py"}
    ingestor = DummyIngestor(module_map)
    resolution = FunctionResolution("api.routes.get_users", "get_users", False)

    assert (
        ingestor._detect_entry_point(
            cast(Node, MockNode("function")),
            resolution,
            module_qn,
            cs.SupportedLanguage.PYTHON,
            ["@app.get"],
        )
        is True
    )


def test_javascript_export_default_entry_point(tmp_path: Path) -> None:
    module_qn = "web.index"
    module_map = {module_qn: tmp_path / "index.js"}
    ingestor = DummyIngestor(module_map)
    resolution = FunctionResolution("web.index.handler", "handler", False)

    assert (
        ingestor._detect_entry_point(
            cast(Node, MockNode("function")),
            resolution,
            module_qn,
            cs.SupportedLanguage.JS,
            ["export default"],
        )
        is True
    )


def test_java_main_entry_point(tmp_path: Path) -> None:
    module_qn = "com.example.App"
    module_map = {module_qn: tmp_path / "App.java"}
    ingestor = DummyIngestor(module_map)
    resolution = FunctionResolution("com.example.App.main", "main", False)

    assert (
        ingestor._detect_entry_point(
            cast(Node, MockNode("function")),
            resolution,
            module_qn,
            cs.SupportedLanguage.JAVA,
            [],
        )
        is True
    )


def test_go_main_entry_point(tmp_path: Path) -> None:
    module_qn = "cmd.app"
    module_map = {module_qn: tmp_path / "main.go"}
    ingestor = DummyIngestor(module_map)
    resolution = FunctionResolution("cmd.app.main", "main", False)

    assert (
        ingestor._detect_entry_point(
            cast(Node, MockNode("function")),
            resolution,
            module_qn,
            cs.SupportedLanguage.GO,
            [],
        )
        is True
    )


def test_ruby_bin_entry_point(tmp_path: Path) -> None:
    module_qn = "bin.task"
    module_map = {module_qn: tmp_path / "bin" / "task"}
    ingestor = DummyIngestor(module_map)
    resolution = FunctionResolution("bin.task.run", "run", False)

    assert (
        ingestor._detect_entry_point(
            cast(Node, MockNode("function")),
            resolution,
            module_qn,
            cs.SupportedLanguage.RUBY,
            [],
        )
        is True
    )


def test_ruby_rake_entry_point(tmp_path: Path) -> None:
    module_qn = "lib.tasks.rake"
    module_map = {module_qn: tmp_path / "lib" / "tasks" / "rake"}
    ingestor = DummyIngestor(module_map)
    resolution = FunctionResolution("lib.tasks.rake.task", "task", False)

    assert (
        ingestor._detect_entry_point(
            cast(Node, MockNode("function")),
            resolution,
            module_qn,
            cs.SupportedLanguage.RUBY,
            [],
        )
        is True
    )
