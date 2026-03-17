from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from codebase_rag.core import constants as cs
from codebase_rag.mcp.tools import MCPToolsRegistry
from codebase_rag.services.graph_service import MemgraphIngestor
from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    EVENT_FLOW_RUNTIME_FIXTURE,
)
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    build_mock_graph_snapshot,
    execute_project_cypher,
    export_project_semantic_snapshot,
    materialize_fixture_repo,
    run_fixture_update,
)

pytestmark = [pytest.mark.integration]


@pytest.fixture(params=["asyncio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
    return str(request.param)


SEMANTIC_NODE_LABELS = {
    cs.NodeLabel.EVENT_FLOW,
    cs.NodeLabel.QUEUE,
    cs.NodeLabel.RUNTIME_ARTIFACT,
    cs.NodeLabel.RUNTIME_EVENT,
}
SEMANTIC_RELATIONSHIP_TYPES = {
    cs.RelationshipType.CONTAINS,
    cs.RelationshipType.OBSERVED_IN_RUNTIME,
    cs.RelationshipType.PUBLISHES_EVENT,
    cs.RelationshipType.USES_HANDLER,
    cs.RelationshipType.USES_QUEUE,
}


def _node_props(mock_ingestor: MagicMock, label: str) -> list[dict[str, object]]:
    return [
        cast(dict[str, object], call.args[1])
        for call in mock_ingestor.ensure_node_batch.call_args_list
        if call.args[0] == label
    ]


def _runtime_fetch_all(
    mock_ingestor: MagicMock,
    query: str,
    parameters: dict[str, object] | None = None,
) -> list[object]:
    project_name = str((parameters or {}).get(cs.KEY_PROJECT_NAME, "")).strip()
    if "MATCH (e:EventFlow" in query:
        return [
            {
                "qualified_name": str(props.get(cs.KEY_QUALIFIED_NAME, "")),
                "canonical_key": str(props.get("canonical_key", "")),
                "event_name": str(props.get("event_name", "")),
                "channel_name": str(props.get("channel_name", "")),
            }
            for props in _node_props(mock_ingestor, cs.NodeLabel.EVENT_FLOW)
            if str(props.get(cs.KEY_PROJECT_NAME, project_name)) == project_name
        ]
    if "MATCH (q:Queue" in query:
        return [
            {
                "qualified_name": str(props.get(cs.KEY_QUALIFIED_NAME, "")),
                "queue_name": str(props.get("queue_name", props.get(cs.KEY_NAME, ""))),
            }
            for props in _node_props(mock_ingestor, cs.NodeLabel.QUEUE)
            if str(props.get(cs.KEY_PROJECT_NAME, project_name)) == project_name
        ]
    if "AND (n:Function OR n:Method)" in query:
        rows: list[dict[str, object]] = []
        for label in (cs.NodeLabel.FUNCTION, cs.NodeLabel.METHOD):
            rows.extend(
                {
                    "labels": [str(label)],
                    "qualified_name": str(props.get(cs.KEY_QUALIFIED_NAME, "")),
                    "name": str(props.get(cs.KEY_NAME, "")),
                }
                for props in _node_props(mock_ingestor, label)
                if str(props.get(cs.KEY_PROJECT_NAME, project_name)) == project_name
            )
        return cast(list[object], rows)
    return []


def test_runtime_event_fixture_snapshot_is_deterministic(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, EVENT_FLOW_RUNTIME_FIXTURE)
    mock_ingestor.fetch_all.side_effect = (
        lambda query, parameters=None: _runtime_fetch_all(  # noqa: E731
            mock_ingestor,
            query,
            cast(dict[str, object] | None, parameters),
        )
    )

    second_ingestor = MagicMock(spec=MemgraphIngestor)
    second_ingestor.fetch_all.side_effect = (
        lambda query, parameters=None: _runtime_fetch_all(
            second_ingestor,
            query,
            cast(dict[str, object] | None, parameters),
        )
    )

    run_fixture_update(fixture_repo, mock_ingestor)
    first_snapshot = build_mock_graph_snapshot(
        mock_ingestor,
        node_labels={str(label) for label in SEMANTIC_NODE_LABELS},
        relationship_types={str(rel) for rel in SEMANTIC_RELATIONSHIP_TYPES},
    )

    run_fixture_update(fixture_repo, second_ingestor)
    second_snapshot = build_mock_graph_snapshot(
        second_ingestor,
        node_labels={str(label) for label in SEMANTIC_NODE_LABELS},
        relationship_types={str(rel) for rel in SEMANTIC_RELATIONSHIP_TYPES},
    )

    assert first_snapshot == second_snapshot

    node_labels = {
        str(node["label"]) for node in first_snapshot["nodes"] if "label" in node
    }
    assert str(cs.NodeLabel.RUNTIME_ARTIFACT) in node_labels
    assert str(cs.NodeLabel.RUNTIME_EVENT) in node_labels

    relationship_types = {
        str(rel["relationship_type"])
        for rel in first_snapshot["relationships"]
        if "relationship_type" in rel
    }
    assert str(cs.RelationshipType.OBSERVED_IN_RUNTIME) in relationship_types
    assert str(cs.RelationshipType.CONTAINS) in relationship_types


@pytest.mark.anyio
async def test_runtime_and_static_event_edges_reconcile_in_memgraph(
    temp_repo: Path,
    memgraph_ingestor: object,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, EVENT_FLOW_RUNTIME_FIXTURE)
    run_fixture_update(fixture_repo, memgraph_ingestor)

    snapshot = export_project_semantic_snapshot(
        memgraph_ingestor,
        project_name=fixture_repo.name,
        node_labels=tuple(str(label) for label in SEMANTIC_NODE_LABELS),
        relationship_types=tuple(str(rel) for rel in SEMANTIC_RELATIONSHIP_TYPES),
    )
    labels = {
        label
        for node in snapshot["nodes"]
        for label in cast(list[str], node.get("labels", []))
    }
    assert str(cs.NodeLabel.RUNTIME_ARTIFACT) in labels
    assert str(cs.NodeLabel.RUNTIME_EVENT) in labels

    rows = execute_project_cypher(
        memgraph_ingestor,
        """
MATCH (flow:EventFlow {project_name: $project_name})
MATCH (flow)-[:OBSERVED_IN_RUNTIME]->(event:RuntimeEvent {project_name: $project_name})
MATCH (event)-[:OBSERVED_IN_RUNTIME]->(handler:Method {project_name: $project_name})
RETURN flow.canonical_key AS canonical_key,
       event.canonical_key AS runtime_key,
       handler.qualified_name AS handler_qn
ORDER BY handler_qn
LIMIT 20
""",
        project_name=fixture_repo.name,
    )
    assert any(
        row.get("canonical_key") == "invoice.created@invoice-events"
        and str(row.get("handler_qn", "")).endswith(
            "InvoiceWorker.handle_invoice_created"
        )
        for row in rows
    )

    registry = MCPToolsRegistry(
        project_root=str(fixture_repo),
        ingestor=cast(object, memgraph_ingestor),
        cypher_gen=MagicMock(),
    )
    registry._session_state["preflight_project_selected"] = True
    registry._session_state["preflight_schema_summary_loaded"] = True

    result = await registry.multi_hop_analysis(
        qualified_name="event_flow_runtime_semantic_fixture.main.dispatch_invoice_created",
        depth=5,
        limit=25,
    )

    assert result.get("status") == "ok"
    affected_symbols = cast(list[str], result.get("affected_symbols", []))
    assert any(
        ".runtime.output.runtime.events.ndjson.event." in symbol
        for symbol in affected_symbols
    )

    hop_summary = cast(dict[str, object], result.get("hop_summary", {}))
    relation_counts = cast(dict[str, object], hop_summary.get("relation_counts", {}))
    assert relation_counts.get("OBSERVED_IN_RUNTIME", 0) >= 1
