from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from codebase_rag.graph_db.cypher_queries import (
    build_config_runtime_query_pack,
    build_event_reliability_query_pack,
    build_frontend_operation_query_pack,
    build_semantic_auth_contract_query_pack,
    build_semantic_validation_query_pack,
    build_test_semantics_query_pack,
)
from codebase_rag.infrastructure.parser_loader import load_parsers
from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    ENV_FLAG_SECRET_FIXTURE,
    EVENT_FLOW_FIXTURE,
    FASTAPI_AUTH_CONTRACT_FIXTURE,
    FRONTEND_OPERATION_FIXTURE,
    QUERY_FINGERPRINT_FIXTURE,
    TEST_SEMANTICS_FIXTURE,
)
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    execute_project_cypher,
    materialize_fixture_repo,
    run_fixture_update,
)

pytestmark = [pytest.mark.integration]


def _require_typescript_parser() -> None:
    parsers, _queries = load_parsers()
    if "typescript" not in parsers:
        pytest.skip("typescript parser not available")


def _entry(
    builder: Callable[[], list[dict[str, object]]],
    name: str,
) -> dict[str, object]:
    for item in builder():
        if item["name"] == name:
            return item
    raise AssertionError(f"Missing semantic query-pack entry: {name}")


def _rows_have_signal(rows: list[dict[str, object]]) -> bool:
    for row in rows:
        for value in row.values():
            if isinstance(value, bool):
                if value:
                    return True
                continue
            if isinstance(value, int | float):
                if value > 0:
                    return True
                continue
            if isinstance(value, list):
                if value:
                    return True
                continue
            if str(value).strip():
                return True
    return False


@pytest.mark.parametrize(
    ("fixture_spec", "builder", "entry_name", "needs_typescript"),
    [
        (
            FASTAPI_AUTH_CONTRACT_FIXTURE,
            build_semantic_auth_contract_query_pack,
            "endpoint_auth_coverage",
            False,
        ),
        (
            EVENT_FLOW_FIXTURE,
            build_event_reliability_query_pack,
            "replay_paths",
            False,
        ),
        (
            FRONTEND_OPERATION_FIXTURE,
            build_frontend_operation_query_pack,
            "client_operations",
            True,
        ),
        (
            TEST_SEMANTICS_FIXTURE,
            build_test_semantics_query_pack,
            "contract_test_coverage",
            False,
        ),
        (
            ENV_FLAG_SECRET_FIXTURE,
            build_config_runtime_query_pack,
            "undefined_env_readers",
            False,
        ),
        (
            QUERY_FINGERPRINT_FIXTURE,
            build_semantic_validation_query_pack,
            "query_fingerprint_minimum",
            False,
        ),
    ],
    ids=lambda item: getattr(item, "name", item if isinstance(item, str) else "query"),
)
def test_semantic_query_pack_smoke(
    temp_repo: Path,
    memgraph_ingestor: object,
    fixture_spec: object,
    builder: Callable[[], list[dict[str, object]]],
    entry_name: str,
    needs_typescript: bool,
) -> None:
    if needs_typescript:
        _require_typescript_parser()

    entry = _entry(builder, entry_name)
    fixture_repo = materialize_fixture_repo(temp_repo, fixture_spec)
    run_fixture_update(fixture_repo, memgraph_ingestor)

    rows = execute_project_cypher(
        memgraph_ingestor,
        str(entry["cypher"]),
        project_name=fixture_repo.name,
    )

    assert rows
    assert _rows_have_signal(rows)
