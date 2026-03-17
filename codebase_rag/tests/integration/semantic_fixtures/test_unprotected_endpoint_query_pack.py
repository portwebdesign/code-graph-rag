from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from codebase_rag.core import constants as cs
from codebase_rag.graph_db.cypher_queries import build_semantic_auth_contract_query_pack
from codebase_rag.mcp.tools import MCPToolsRegistry
from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    FASTAPI_AUTH_CONTRACT_FIXTURE,
)
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    execute_project_cypher,
    export_project_semantic_snapshot,
    materialize_fixture_repo,
    run_fixture_update,
)

pytestmark = [pytest.mark.integration]


@pytest.fixture(params=["asyncio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
    return str(request.param)


SEMANTIC_NODE_LABELS = (
    str(cs.NodeLabel.ENDPOINT),
    str(cs.NodeLabel.DEPENDENCY_PROVIDER),
    str(cs.NodeLabel.AUTH_POLICY),
    str(cs.NodeLabel.AUTH_SCOPE),
    str(cs.NodeLabel.CONTRACT),
)
SEMANTIC_RELATIONSHIP_TYPES = (
    str(cs.RelationshipType.USES_DEPENDENCY),
    str(cs.RelationshipType.SECURED_BY),
    str(cs.RelationshipType.REQUIRES_SCOPE),
    str(cs.RelationshipType.ACCEPTS_CONTRACT),
    str(cs.RelationshipType.RETURNS_CONTRACT),
)


def _query_from_pack(name: str) -> str:
    for item in build_semantic_auth_contract_query_pack():
        if item["name"] == name:
            return item["cypher"]
    raise AssertionError(f"Missing semantic query preset: {name}")


def test_fastapi_semantic_query_pack_smoke(
    temp_repo: Path,
    memgraph_ingestor: object,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, FASTAPI_AUTH_CONTRACT_FIXTURE)
    run_fixture_update(fixture_repo, memgraph_ingestor)

    snapshot = export_project_semantic_snapshot(
        memgraph_ingestor,
        project_name=fixture_repo.name,
        node_labels=SEMANTIC_NODE_LABELS,
        relationship_types=SEMANTIC_RELATIONSHIP_TYPES,
    )

    snapshot_labels = {
        str(label)
        for node in snapshot["nodes"]
        for label in cast(list[str], node["labels"])
    }
    assert {
        str(cs.NodeLabel.ENDPOINT),
        str(cs.NodeLabel.DEPENDENCY_PROVIDER),
        str(cs.NodeLabel.AUTH_POLICY),
        str(cs.NodeLabel.AUTH_SCOPE),
        str(cs.NodeLabel.CONTRACT),
    }.issubset(snapshot_labels)

    snapshot_relationships = {
        str(rel["relationship_type"]) for rel in snapshot["relationships"]
    }
    assert {
        str(cs.RelationshipType.USES_DEPENDENCY),
        str(cs.RelationshipType.SECURED_BY),
        str(cs.RelationshipType.REQUIRES_SCOPE),
        str(cs.RelationshipType.ACCEPTS_CONTRACT),
        str(cs.RelationshipType.RETURNS_CONTRACT),
    }.issubset(snapshot_relationships)

    unprotected_rows = execute_project_cypher(
        memgraph_ingestor,
        _query_from_pack("unprotected_endpoints"),
        project_name=fixture_repo.name,
    )
    assert {str(row["endpoint"]) for row in unprotected_rows} == {"/api/health"}

    coverage_rows = execute_project_cypher(
        memgraph_ingestor,
        _query_from_pack("endpoint_auth_coverage"),
        project_name=fixture_repo.name,
    )
    invoice_row = next(
        row for row in coverage_rows if str(row["endpoint"]) == "/api/invoices"
    )
    assert invoice_row["policy_count"] == 1
    assert invoice_row["scope_count"] == 1

    contract_gap_rows = execute_project_cypher(
        memgraph_ingestor,
        _query_from_pack("endpoint_contract_gaps"),
        project_name=fixture_repo.name,
    )
    gap_endpoints = {str(row["endpoint"]) for row in contract_gap_rows}
    assert "/api/health" in gap_endpoints
    assert "/api/invoices" not in gap_endpoints


@pytest.mark.anyio
async def test_fastapi_semantic_multi_hop_smoke(
    temp_repo: Path,
    memgraph_ingestor: object,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, FASTAPI_AUTH_CONTRACT_FIXTURE)
    run_fixture_update(fixture_repo, memgraph_ingestor)

    registry = MCPToolsRegistry(
        project_root=str(fixture_repo),
        ingestor=cast(object, memgraph_ingestor),
        cypher_gen=MagicMock(),
    )
    registry._session_state["preflight_project_selected"] = True
    registry._session_state["preflight_schema_summary_loaded"] = True

    result = await registry.multi_hop_analysis(
        qualified_name="fastapi_semantic_fixture.endpoint.fastapi.POST:/api/invoices",
        depth=3,
        limit=20,
    )

    assert result.get("status") == "ok"
    hop_summary = cast(dict[str, object], result.get("hop_summary", {}))
    relation_counts = cast(dict[str, object], hop_summary.get("relation_counts", {}))
    assert relation_counts.get("USES_DEPENDENCY", 0) >= 1
    assert relation_counts.get("SECURED_BY", 0) >= 1
    assert relation_counts.get("RETURNS_CONTRACT", 0) >= 1

    affected_symbols = cast(list[str], result.get("affected_symbols", []))
    assert any("InvoiceResponse" in symbol for symbol in affected_symbols)
