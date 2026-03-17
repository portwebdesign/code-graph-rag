from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from codebase_rag.core.semantic_schema_metadata import (
    SEMANTIC_SCHEMA_VERSION,
    build_semantic_schema_metadata,
)
from codebase_rag.graph_db.cypher_queries import build_semantic_validation_query_pack
from codebase_rag.mcp.tools import MCPToolsRegistry


def test_semantic_schema_metadata_tracks_validation_queries() -> None:
    metadata = build_semantic_schema_metadata()

    assert metadata["schema_version"] == SEMANTIC_SCHEMA_VERSION

    capabilities = cast(list[dict[str, object]], metadata["capabilities"])
    capability_ids = [str(item["id"]) for item in capabilities]
    assert capability_ids == [
        "fastapi_auth_contract",
        "event_flow",
        "runtime_reconciliation",
        "transaction_flow",
        "query_fingerprint",
        "frontend_operation_governance",
        "test_semantics",
        "config_control_plane",
    ]

    validation_query_names = {
        str(entry["name"]) for entry in build_semantic_validation_query_pack()
    }
    surfaced_validation_queries = {
        str(name)
        for capability in capabilities
        for name in cast(list[str], capability.get("validation_queries", []))
    }
    assert validation_query_names <= surfaced_validation_queries


@pytest.mark.anyio
async def test_get_schema_overview_exposes_semantic_schema_metadata() -> None:
    ingestor = MagicMock()
    ingestor.fetch_all.side_effect = [
        [
            {
                "from_node_type": "Endpoint",
                "relationship_type": "SECURED_BY",
                "to_node_type": "AuthPolicy",
            },
            {
                "from_node_type": "Function",
                "relationship_type": "PUBLISHES_EVENT",
                "to_node_type": "EventFlow",
            },
        ],
        [
            {"label": "Endpoint", "count": 2},
            {"label": "Contract", "count": 3},
            {"label": "EventFlow", "count": 1},
        ],
    ]

    registry = MCPToolsRegistry(
        project_root=str(Path(__file__).resolve().parents[4]),
        ingestor=ingestor,
        cypher_gen=MagicMock(),
    )
    registry._session_state["preflight_project_selected"] = True
    registry._session_state["preflight_schema_summary_loaded"] = True

    result = await registry.get_schema_overview(scope="api")

    assert result.get("status") == "ok"
    semantic_schema = cast(dict[str, object], result.get("semantic_schema", {}))
    assert semantic_schema.get("schema_version") == SEMANTIC_SCHEMA_VERSION

    planes = cast(list[dict[str, object]], semantic_schema.get("signal_planes", []))
    assert {str(item.get("name", "")) for item in planes} == {
        "static",
        "runtime",
        "heuristic",
    }

    capabilities = cast(
        list[dict[str, object]], semantic_schema.get("capabilities", [])
    )
    assert any(item.get("id") == "runtime_reconciliation" for item in capabilities)
