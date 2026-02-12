from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from codebase_rag.analysis.analysis_runner import AnalysisRunner
from codebase_rag.analysis.modules.base_module import AnalysisContext
from codebase_rag.analysis.modules.schema_validator import SchemaValidatorModule
from codebase_rag.analysis.types import NodeRecord, RelationshipRecord
from codebase_rag.core import constants as cs


def test_schema_validator_counts() -> None:
    nodes = [
        NodeRecord(node_id=1, labels=[cs.NodeLabel.PROJECT.value], properties={}),
        NodeRecord(node_id=2, labels=[cs.NodeLabel.FUNCTION.value], properties={}),
        NodeRecord(node_id=3, labels=[cs.NodeLabel.FUNCTION.value], properties={}),
        NodeRecord(node_id=4, labels=[cs.NodeLabel.TYPE.value], properties={}),
    ]
    relationships = [
        RelationshipRecord(
            from_id=1,
            to_id=2,
            rel_type=cs.RelationshipType.CONTAINS.value,
            properties={},
        ),
        RelationshipRecord(
            from_id=2,
            to_id=4,
            rel_type=cs.RelationshipType.RETURNS_TYPE.value,
            properties={},
        ),
    ]

    context = AnalysisContext(
        runner=cast(AnalysisRunner, SimpleNamespace()),
        nodes=nodes,
        relationships=relationships,
        module_path_map={},
        node_by_id={node.node_id: node for node in nodes},
        module_paths=None,
        incremental_paths=None,
        use_db=False,
        summary={},
        dead_code_verifier=None,
    )

    module = SchemaValidatorModule()
    result = module.run(context)

    assert result["orphan_nodes"] == 2
    assert result["missing_types"] == 1
    assert result["broken_refs"] == 0
