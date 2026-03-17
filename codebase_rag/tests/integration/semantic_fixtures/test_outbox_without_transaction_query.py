from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from codebase_rag.graph_db.cypher_queries import build_event_reliability_query_pack
from codebase_rag.mcp.tools import MCPToolsRegistry
from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    EVENT_RELIABILITY_RISK_FIXTURE,
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
    for item in build_event_reliability_query_pack():
        if item["name"] == name:
            return item["cypher"]
    raise AssertionError(f"Missing event reliability query preset: {name}")


def test_event_reliability_query_pack_smoke(
    temp_repo: Path,
    memgraph_ingestor: object,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, EVENT_RELIABILITY_RISK_FIXTURE)
    run_fixture_update(fixture_repo, memgraph_ingestor)

    outbox_rows = execute_project_cypher(
        memgraph_ingestor,
        _query_from_pack("outbox_without_transaction"),
        project_name=fixture_repo.name,
    )
    assert any(
        str(row["producer"]).endswith(".persist_invoice_outbox") for row in outbox_rows
    )

    consumer_rows = execute_project_cypher(
        memgraph_ingestor,
        _query_from_pack("consumer_without_dlq"),
        project_name=fixture_repo.name,
    )
    assert any(
        str(row["handler"]).endswith(".InvoiceWorker.handle_invoice_created")
        for row in consumer_rows
    )

    replay_rows = execute_project_cypher(
        memgraph_ingestor,
        _query_from_pack("replay_paths"),
        project_name=fixture_repo.name,
    )
    assert any(
        str(row["replayer"]).endswith(".replay_invoice_created") for row in replay_rows
    )

    transaction_rows = execute_project_cypher(
        memgraph_ingestor,
        _query_from_pack("external_call_before_commit"),
        project_name=fixture_repo.name,
    )
    assert any(
        str(row["actor"]).endswith(".persist_with_external_call_before_commit")
        for row in transaction_rows
    )

    duplicate_rows = execute_project_cypher(
        memgraph_ingestor,
        _query_from_pack("duplicate_publishers"),
        project_name=fixture_repo.name,
    )
    invoice_publishers = next(
        row for row in duplicate_rows if str(row["event_name"]) == "invoice.created"
    )
    assert int(invoice_publishers["publisher_count"]) >= 2


@pytest.mark.anyio
async def test_event_reliability_presets_are_exposed_in_schema_overview(
    temp_repo: Path,
    memgraph_ingestor: object,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, EVENT_RELIABILITY_RISK_FIXTURE)
    run_fixture_update(fixture_repo, memgraph_ingestor)

    registry = MCPToolsRegistry(
        project_root=str(fixture_repo),
        ingestor=memgraph_ingestor,
        cypher_gen=MagicMock(),
    )
    registry._session_state["preflight_project_selected"] = True
    registry._session_state["preflight_schema_summary_loaded"] = True

    result = await registry.get_schema_overview(scope="api")
    presets = result.get("semantic_cypher_presets", [])

    assert isinstance(presets, list)
    preset_names = {str(item.get("name")) for item in presets if isinstance(item, dict)}
    assert "outbox_without_transaction" in preset_names
    assert "consumer_without_dlq" in preset_names
    assert "external_call_before_commit" in preset_names
