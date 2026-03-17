from __future__ import annotations

from pathlib import Path

import pytest

from codebase_rag.graph_db.cypher_queries import build_config_runtime_query_pack
from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    ENV_FLAG_SECRET_FIXTURE,
)
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    execute_project_cypher,
    materialize_fixture_repo,
    run_fixture_update,
)

pytestmark = [pytest.mark.integration]


def _query_from_pack(name: str) -> str:
    for item in build_config_runtime_query_pack():
        if item["name"] == name:
            return item["cypher"]
    raise AssertionError(f"Missing config/runtime query preset: {name}")


def test_orphan_feature_flag_query_returns_reader_only_and_resource_only_flags(
    temp_repo: Path,
    memgraph_ingestor: object,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, ENV_FLAG_SECRET_FIXTURE)
    run_fixture_update(fixture_repo, memgraph_ingestor)

    rows = execute_project_cypher(
        memgraph_ingestor,
        _query_from_pack("orphan_feature_flags"),
        project_name=fixture_repo.name,
    )

    assert any(
        str(row["flag_name"]) == "FEATURE_EXPERIMENTAL"
        and str(row["drift_kind"]) == "reader_only"
        for row in rows
    )
    assert any(
        str(row["flag_name"]) == "FEATURE_UNUSED"
        and str(row["drift_kind"]) == "resource_only"
        for row in rows
    )


def test_resource_without_reader_and_reader_without_resource_queries_return_expected_rows(
    temp_repo: Path,
    memgraph_ingestor: object,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, ENV_FLAG_SECRET_FIXTURE)
    run_fixture_update(fixture_repo, memgraph_ingestor)

    resource_rows = execute_project_cypher(
        memgraph_ingestor,
        _query_from_pack("resource_without_readers"),
        project_name=fixture_repo.name,
    )
    assert any(
        str(row["env_name"]) == "PUBLIC_CACHE_URL"
        and "compose_service.api" in str(row["resource_qn"])
        for row in resource_rows
    )
    assert any(
        str(row["env_name"]) == "STRIPE_SECRET"
        and "compose_service.api" in str(row["resource_qn"])
        for row in resource_rows
    )

    reader_rows = execute_project_cypher(
        memgraph_ingestor,
        _query_from_pack("reader_without_resource"),
        project_name=fixture_repo.name,
    )
    assert any(
        str(row["env_name"]) == "MISSING_ANALYTICS_KEY"
        and str(row["reader"]).endswith(".analytics_key")
        and bool(row["has_definition"]) is False
        for row in reader_rows
    )
    assert any(
        str(row["env_name"]) == "FEATURE_EXPERIMENTAL"
        and bool(row["has_definition"]) is False
        for row in reader_rows
    )
