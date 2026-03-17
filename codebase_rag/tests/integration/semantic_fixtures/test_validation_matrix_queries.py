from __future__ import annotations

from pathlib import Path

import pytest

from codebase_rag.graph_db.cypher_queries import build_semantic_validation_query_pack
from codebase_rag.infrastructure.parser_loader import load_parsers
from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    ENV_FLAG_SECRET_FIXTURE,
    EVENT_FLOW_FIXTURE,
    FASTAPI_AUTH_CONTRACT_FIXTURE,
    FRONTEND_OPERATION_FIXTURE,
    QUERY_FINGERPRINT_FIXTURE,
    TEST_SEMANTICS_FIXTURE,
    TRANSACTION_FLOW_FIXTURE,
)
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    execute_project_cypher,
    materialize_fixture_repo,
    run_fixture_update,
)

pytestmark = [pytest.mark.integration]

_FIXTURES_BY_NAME = {
    "fastapi_semantic_fixture": FASTAPI_AUTH_CONTRACT_FIXTURE,
    "event_flow_semantic_fixture": EVENT_FLOW_FIXTURE,
    "transaction_flow_semantic_fixture": TRANSACTION_FLOW_FIXTURE,
    "query_fingerprint_semantic_fixture": QUERY_FINGERPRINT_FIXTURE,
    "frontend_operation_semantic_fixture": FRONTEND_OPERATION_FIXTURE,
    "test_semantics_fixture": TEST_SEMANTICS_FIXTURE,
    "env_flag_secret_semantic_fixture": ENV_FLAG_SECRET_FIXTURE,
}

_TYPESCRIPT_FIXTURES = {"frontend_operation_semantic_fixture"}


def _validation_entry(name: str) -> dict[str, object]:
    for entry in build_semantic_validation_query_pack():
        if entry["name"] == name:
            return entry
    raise AssertionError(f"Missing validation query entry: {name}")


def _require_typescript_parser() -> None:
    parsers, _queries = load_parsers()
    if "typescript" not in parsers:
        pytest.skip("typescript parser not available")


@pytest.mark.parametrize(
    ("entry_name", "fixture_name"),
    [
        ("fastapi_auth_contract_minimum", "fastapi_semantic_fixture"),
        ("event_flow_minimum", "event_flow_semantic_fixture"),
        ("transaction_flow_minimum", "transaction_flow_semantic_fixture"),
        ("query_fingerprint_minimum", "query_fingerprint_semantic_fixture"),
        ("frontend_operation_minimum", "frontend_operation_semantic_fixture"),
        ("test_semantics_minimum", "test_semantics_fixture"),
        ("config_control_plane_minimum", "env_flag_secret_semantic_fixture"),
    ],
)
def test_validation_matrix_queries_return_minimum_rows(
    temp_repo: Path,
    memgraph_ingestor: object,
    entry_name: str,
    fixture_name: str,
) -> None:
    if fixture_name in _TYPESCRIPT_FIXTURES:
        _require_typescript_parser()

    entry = _validation_entry(entry_name)
    fixture_repo = materialize_fixture_repo(temp_repo, _FIXTURES_BY_NAME[fixture_name])
    run_fixture_update(fixture_repo, memgraph_ingestor)

    rows = execute_project_cypher(
        memgraph_ingestor,
        str(entry["cypher"]),
        project_name=fixture_repo.name,
    )

    assert rows
    matched_rows = int(rows[0].get("matched_rows", 0))
    assert matched_rows >= int(entry["minimum_rows"])
