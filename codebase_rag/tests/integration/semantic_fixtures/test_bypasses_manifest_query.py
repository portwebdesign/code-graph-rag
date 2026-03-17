from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from codebase_rag.graph_db.cypher_queries import build_frontend_operation_query_pack
from codebase_rag.mcp.tools import MCPToolsRegistry
from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    FRONTEND_OPERATION_FIXTURE,
)
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    execute_project_cypher,
    materialize_fixture_repo,
    run_fixture_update,
)

pytestmark = [pytest.mark.integration]


@pytest.fixture(params=["asyncio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
    return str(request.param)


def _query_from_pack(name: str) -> str:
    for item in build_frontend_operation_query_pack():
        if item["name"] == name:
            return item["cypher"]
    raise AssertionError(f"Missing frontend operation query preset: {name}")


def test_bypasses_manifest_query_pack_smoke(
    temp_repo: Path,
    memgraph_ingestor: object,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, FRONTEND_OPERATION_FIXTURE)
    run_fixture_update(fixture_repo, memgraph_ingestor)

    rows = execute_project_cypher(
        memgraph_ingestor,
        _query_from_pack("bypasses_manifest"),
        project_name=fixture_repo.name,
    )
    assert any(str(row["operation_id"]) == "createOrder" for row in rows)


@pytest.mark.anyio
async def test_frontend_operation_presets_are_exposed_in_schema_overview(
    temp_repo: Path,
    memgraph_ingestor: object,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, FRONTEND_OPERATION_FIXTURE)
    run_fixture_update(fixture_repo, memgraph_ingestor)

    registry = MCPToolsRegistry(
        project_root=str(fixture_repo),
        ingestor=memgraph_ingestor,
        cypher_gen=MagicMock(),
    )
    registry._session_state["preflight_project_selected"] = True
    registry._session_state["preflight_schema_summary_loaded"] = True

    result = await registry.get_schema_overview(scope="frontend")
    presets = result.get("semantic_cypher_presets", [])

    assert isinstance(presets, list)
    preset_names = {str(item.get("name")) for item in presets if isinstance(item, dict)}
    assert "client_operations" in preset_names
    assert "bypasses_manifest" in preset_names
