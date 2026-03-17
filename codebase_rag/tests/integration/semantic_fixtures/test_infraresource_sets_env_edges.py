from __future__ import annotations

from pathlib import Path

import pytest

from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    ENV_FLAG_SECRET_FIXTURE,
)
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    execute_project_cypher,
    materialize_fixture_repo,
    run_fixture_update,
)

pytestmark = [pytest.mark.integration]


def test_infraresource_projects_sets_env_edges(
    temp_repo: Path,
    memgraph_ingestor: object,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, ENV_FLAG_SECRET_FIXTURE)
    run_fixture_update(fixture_repo, memgraph_ingestor)

    rows = execute_project_cypher(
        memgraph_ingestor,
        """
MATCH (resource:InfraResource {project_name: $project_name})-[:SETS_ENV]->(env:EnvVar {project_name: $project_name})
RETURN resource.qualified_name AS resource_qn,
       env.name AS env_name
ORDER BY resource_qn, env_name
LIMIT 50
""",
        project_name=fixture_repo.name,
    )

    assert any(
        "compose_service.api" in str(row["resource_qn"])
        and str(row["env_name"]) == "APP_SECRET"
        for row in rows
    )
    assert any(
        "k8s.deployment.api" in str(row["resource_qn"])
        and str(row["env_name"]) == "NEXT_PUBLIC_API_URL"
        for row in rows
    )
