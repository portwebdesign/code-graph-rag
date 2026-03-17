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


def test_undefined_env_query_returns_readers_without_definition(
    temp_repo: Path,
    memgraph_ingestor: object,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, ENV_FLAG_SECRET_FIXTURE)
    run_fixture_update(fixture_repo, memgraph_ingestor)

    rows = execute_project_cypher(
        memgraph_ingestor,
        _query_from_pack("undefined_env_readers"),
        project_name=fixture_repo.name,
    )

    assert any(
        str(row["env_name"]) == "MISSING_ANALYTICS_KEY"
        and str(row["reader"]).endswith(".analytics_key")
        for row in rows
    )


def test_unbound_secret_query_can_detect_provider_only_secret_refs(
    temp_repo: Path,
    memgraph_ingestor: object,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, ENV_FLAG_SECRET_FIXTURE)
    run_fixture_update(fixture_repo, memgraph_ingestor)

    rows = execute_project_cypher(
        memgraph_ingestor,
        _query_from_pack("unbound_secret_refs"),
        project_name=fixture_repo.name,
    )

    assert any(
        str(row["secret_name"]) == "api-secrets"
        and str(row["binding_status"]) == "resource_only"
        for row in rows
    )
