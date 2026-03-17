from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from codebase_rag.tests.conftest import run_updater
from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    ENV_FLAG_SECRET_FIXTURE,
)
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    materialize_fixture_repo,
)


def test_semantic_graph_payloads_never_persist_secret_values(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = materialize_fixture_repo(temp_repo, ENV_FLAG_SECRET_FIXTURE)
    mock_ingestor.fetch_all.return_value = []

    run_updater(project, mock_ingestor)

    payloads: list[object] = []
    for call in mock_ingestor.ensure_node_batch.call_args_list:
        payloads.append(call.args[1])
    for call in mock_ingestor.ensure_relationship_batch.call_args_list:
        if len(call.args) > 3 and isinstance(call.args[3], dict):
            payloads.append(call.args[3])

    serialized = " ".join(str(item) for item in payloads)
    assert "super-secret" not in serialized
    assert "sk_live_fixture_secret" not in serialized
    assert "<redacted>" in serialized
