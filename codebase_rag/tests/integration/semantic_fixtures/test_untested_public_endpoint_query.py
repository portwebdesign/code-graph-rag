from __future__ import annotations

from pathlib import Path

import pytest

from codebase_rag.graph_db.cypher_queries import build_test_semantics_query_pack
from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    TEST_SEMANTICS_FIXTURE,
)
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    execute_project_cypher,
    materialize_fixture_repo,
    run_fixture_update,
)

pytestmark = [pytest.mark.integration]


def _query_from_pack(name: str) -> str:
    for item in build_test_semantics_query_pack():
        if item["name"] == name:
            return item["cypher"]
    raise AssertionError(f"Missing test semantics query preset: {name}")


def test_untested_public_endpoint_query_flags_only_uncovered_routes(
    temp_repo: Path,
    memgraph_ingestor: object,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, TEST_SEMANTICS_FIXTURE)
    run_fixture_update(fixture_repo, memgraph_ingestor)

    rows = execute_project_cypher(
        memgraph_ingestor,
        _query_from_pack("untested_public_endpoints"),
        project_name=fixture_repo.name,
    )

    endpoints = {(str(row["method"]), str(row["endpoint"])) for row in rows}
    assert ("GET", "/api/health") in endpoints
    assert ("POST", "/api/orders") not in endpoints


def test_contract_test_coverage_query_surfaces_order_contracts(
    temp_repo: Path,
    memgraph_ingestor: object,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, TEST_SEMANTICS_FIXTURE)
    run_fixture_update(fixture_repo, memgraph_ingestor)

    rows = execute_project_cypher(
        memgraph_ingestor,
        _query_from_pack("contract_test_coverage"),
        project_name=fixture_repo.name,
    )

    order_response = next(
        row for row in rows if str(row["contract_name"]) == "OrderResponse"
    )
    assert int(order_response["testcase_count"]) >= 1
    test_files = [str(item) for item in row_cast(order_response, "test_files")]
    assert any(path.endswith("tests/test_orders.py") for path in test_files)


def row_cast(row: dict[str, object], key: str) -> list[object]:
    value = row.get(key, [])
    return value if isinstance(value, list) else []
