from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from codebase_rag.core import constants as cs
from codebase_rag.parsers.pipeline.semantic_pass_registry import (
    SemanticPassContext,
    SemanticPassDefinition,
    SemanticPassRegistry,
    default_semantic_pass_definitions,
)


class _DummySemanticPass:
    def __init__(self, pass_id: str, executed: list[str]) -> None:
        self.pass_id = pass_id
        self.executed = executed

    def process_ast_cache(self, ast_cache_items) -> None:
        tuple(ast_cache_items)
        self.executed.append(self.pass_id)


def test_default_semantic_pass_registry_order_is_stable() -> None:
    definitions = default_semantic_pass_definitions()
    assert [definition.pass_id for definition in definitions] == [
        "contract_semantics",
        "event_flow_semantics",
        "query_fingerprint_semantics",
        "transaction_flow_semantics",
        "frontend_operation_semantics",
        "config_semantics",
        "test_semantics",
    ]


def test_semantic_pass_registry_runs_passes_in_declared_order(monkeypatch) -> None:
    monkeypatch.setenv("CGR_PASS_ALPHA", "1")
    monkeypatch.setenv("CGR_PASS_BRAVO", "1")

    executed: list[str] = []
    context = SemanticPassContext(
        ingestor=object(),
        repo_path=Path("demo"),
        project_name="demo",
        function_registry={},
    )
    registry = SemanticPassRegistry(
        context,
        definitions=(
            SemanticPassDefinition(
                pass_id="bravo",
                display_name="bravo pass",
                env_flag="CGR_PASS_BRAVO",
                order=200,
                factory=lambda _context: _DummySemanticPass("bravo", executed),
            ),
            SemanticPassDefinition(
                pass_id="alpha",
                display_name="alpha pass",
                env_flag="CGR_PASS_ALPHA",
                order=100,
                factory=lambda _context: _DummySemanticPass("alpha", executed),
            ),
        ),
    )

    registry.run_enabled(
        ((Path("main.py"), (MagicMock(), cs.SupportedLanguage.PYTHON)),)
    )

    assert [definition.pass_id for definition in registry.ordered_definitions()] == [
        "alpha",
        "bravo",
    ]
    assert executed == ["alpha", "bravo"]
