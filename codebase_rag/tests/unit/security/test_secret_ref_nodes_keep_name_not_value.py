from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from codebase_rag.core import constants as cs
from codebase_rag.tests.conftest import get_nodes, run_updater
from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    ENV_FLAG_SECRET_FIXTURE,
)
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    materialize_fixture_repo,
)


def test_secret_ref_nodes_keep_metadata_not_raw_secret_values(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = materialize_fixture_repo(temp_repo, ENV_FLAG_SECRET_FIXTURE)
    mock_ingestor.fetch_all.return_value = []

    run_updater(project, mock_ingestor)

    secret_nodes = [
        call.args[1] for call in get_nodes(mock_ingestor, cs.NodeLabel.SECRET_REF)
    ]
    assert secret_nodes

    app_secret = next(
        props
        for props in secret_nodes
        if str(props.get(cs.KEY_NAME, "")) == "APP_SECRET"
    )
    provider_secret = next(
        props
        for props in secret_nodes
        if str(props.get(cs.KEY_NAME, "")) == "api-secrets"
    )

    assert app_secret.get("masked") is True
    assert app_secret.get(cs.KEY_NAME) == "APP_SECRET"
    assert "value" not in app_secret

    assert provider_secret.get("masked") is True
    assert provider_secret.get("secret_key") == "app-secret"
    assert provider_secret.get(cs.KEY_NAME) == "api-secrets"

    serialized = " ".join(str(props) for props in secret_nodes)
    assert "super-secret" not in serialized
    assert "sk_live_fixture_secret" not in serialized
