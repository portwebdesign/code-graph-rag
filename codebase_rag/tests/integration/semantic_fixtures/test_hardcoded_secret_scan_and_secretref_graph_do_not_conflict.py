from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from codebase_rag.core import constants as cs
from codebase_rag.core.config_semantic_identity import normalize_env_name
from codebase_rag.security.security_scanner import SecurityScanner
from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    ENV_FLAG_SECRET_FIXTURE,
)
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    build_mock_graph_snapshot,
    materialize_fixture_repo,
    run_fixture_update,
)


def test_hardcoded_secret_scan_aligns_with_secretref_graph_without_raw_values(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, ENV_FLAG_SECRET_FIXTURE)
    mock_ingestor.fetch_all.return_value = []

    run_fixture_update(fixture_repo, mock_ingestor)

    snapshot = build_mock_graph_snapshot(
        mock_ingestor,
        node_labels={str(cs.NodeLabel.SECRET_REF)},
    )
    graph_secret_names = {
        normalize_env_name(str(node["props"].get("name", "")))
        for node in snapshot["nodes"]
        if node["label"] == str(cs.NodeLabel.SECRET_REF)
    }

    scanner = SecurityScanner()
    findings = []
    for file_path in fixture_repo.rglob("*"):
        if not file_path.is_file():
            continue
        findings.extend(
            scanner.scan_secret_text(
                file_path.read_text(encoding="utf-8", errors="ignore"),
                file_path.relative_to(fixture_repo).as_posix(),
            )
        )

    payloads = [finding.to_payload() for finding in findings]
    scanned_secret_names = {
        str(item["secret_name"])
        for item in payloads
        if isinstance(item.get("secret_name"), str)
    }

    assert "APP_SECRET" in scanned_secret_names
    assert "STRIPE_SECRET" in scanned_secret_names
    assert scanned_secret_names <= graph_secret_names

    serialized = " ".join(str(item) for item in payloads)
    assert "super-secret" not in serialized
    assert "sk_live_fixture_secret" not in serialized
