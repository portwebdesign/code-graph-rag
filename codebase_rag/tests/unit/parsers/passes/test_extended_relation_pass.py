from __future__ import annotations

from pathlib import Path
from typing import cast

from codebase_rag.core import constants as cs
from codebase_rag.data_models.types_defs import ASTNode
from codebase_rag.parsers.pipeline.extended_relation_pass import ExtendedRelationPass
from codebase_rag.parsers.type_inference.enhanced_function_extractor import (
    FunctionMetadata,
)
from codebase_rag.tests.conftest import create_mock_node


class FakeIngestor:
    def __init__(self) -> None:
        self.nodes: list[tuple[str, dict[str, object]]] = []
        self.relationships: list[tuple[tuple, str, tuple]] = []

    def ensure_node_batch(self, label, props) -> None:
        self.nodes.append((label, props))

    def ensure_relationship_batch(self, source, rel_type, target, props=None) -> None:
        self.relationships.append((source, rel_type.value, target))


def test_extended_relation_pass(monkeypatch, tmp_path: Path) -> None:
    metadata = FunctionMetadata(
        qualified_name="pkg.module.func",
        label=cs.NodeLabel.FUNCTION,
        module_qn="pkg.module",
        name="func",
        decorators=["@route"],
        return_type="str",
        parameter_types=[("value", "int")],
        thrown_exceptions=["ValueError"],
        caught_exceptions=["KeyError"],
    )

    def fake_extract(*_args, **_kwargs):
        return [metadata]

    monkeypatch.setattr(
        "codebase_rag.parsers.enhanced_function_extractor.EnhancedFunctionExtractor.extract_from_ast",
        fake_extract,
    )

    ingestor = FakeIngestor()
    pass_runner = ExtendedRelationPass(
        ingestor=ingestor,
        repo_path=tmp_path,
        project_name="pkg",
        queries={},
    )

    pass_runner.process_ast_cache(
        [
            (
                tmp_path / "mod.py",
                (
                    cast(ASTNode, create_mock_node("module")),
                    cs.SupportedLanguage.PYTHON,
                ),
            )
        ]
    )

    rel_types = {rel[1] for rel in ingestor.relationships}
    assert cs.RelationshipType.RETURNS_TYPE.value in rel_types
    assert cs.RelationshipType.PARAMETER_TYPE.value in rel_types
    assert cs.RelationshipType.DECORATES.value in rel_types
    assert cs.RelationshipType.THROWS.value in rel_types
    assert cs.RelationshipType.CAUGHT_BY.value in rel_types
