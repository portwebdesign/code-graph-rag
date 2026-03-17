from __future__ import annotations

from pathlib import Path

from codebase_rag.core import constants as cs
from codebase_rag.tests.conftest import get_nodes, run_updater
from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    ENV_FLAG_SECRET_FIXTURE,
)
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    materialize_fixture_repo,
)


def test_secret_ref_nodes_and_infra_payloads_redact_secret_values(
    temp_repo: Path,
    mock_ingestor,
) -> None:
    project = materialize_fixture_repo(temp_repo, ENV_FLAG_SECRET_FIXTURE)
    mock_ingestor.fetch_all.return_value = []

    run_updater(project, mock_ingestor)

    secret_nodes = [
        call.args[1] for call in get_nodes(mock_ingestor, cs.NodeLabel.SECRET_REF)
    ]
    assert secret_nodes
    assert all(props.get("masked") is True for props in secret_nodes)
    assert all("super-secret" not in str(props) for props in secret_nodes)

    infra_nodes = [
        call.args[1] for call in get_nodes(mock_ingestor, cs.NodeLabel.INFRA_RESOURCE)
    ]
    assert infra_nodes
    assert any(
        "<redacted>" in str(props.get("environment", {})) for props in infra_nodes
    )
    assert all("super-secret" not in str(props) for props in infra_nodes)
