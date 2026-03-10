from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from codebase_rag.services.mcp_benchmark import run_mcp_benchmarks


@pytest.mark.anyio
async def test_run_mcp_benchmarks_reports_client_profiles(
    tmp_path: Path,
    mock_ingestor: MagicMock,
) -> None:
    repo_path = tmp_path / "sample_repo"
    repo_path.mkdir()

    mock_ingestor.list_projects.return_value = [repo_path.name]
    mock_ingestor.fetch_all.return_value = []

    report = await run_mcp_benchmarks(repo_path, mock_ingestor)

    assert report["status"] == "ok"
    assert report["project_name"] == repo_path.name
    client_profiles = cast(list[dict[str, object]], report["client_profiles"])
    assert isinstance(client_profiles, list)
    assert len(client_profiles) == 6
    regression_suite = cast(dict[str, object], report["regression_suite"])
    regression_checks = cast(dict[str, object], regression_suite["checks"])
    assert regression_checks["state_machine_published"] is True
    ollama_row = next(row for row in client_profiles if row["profile"] == "ollama")
    response_profile = cast(dict[str, object], ollama_row["response_profile"])
    assert response_profile["default_output_mode"] == "plan_json"


@pytest.mark.anyio
async def test_run_mcp_benchmarks_writes_json_report(
    tmp_path: Path,
    mock_ingestor: MagicMock,
) -> None:
    repo_path = tmp_path / "bench_repo"
    repo_path.mkdir()
    output_path = tmp_path / "reports" / "mcp-benchmark.json"

    mock_ingestor.list_projects.return_value = [repo_path.name]
    mock_ingestor.fetch_all.return_value = []

    report = await run_mcp_benchmarks(
        repo_path,
        mock_ingestor,
        output_path=output_path,
    )

    assert output_path.exists()
    persisted = json.loads(output_path.read_text(encoding="utf-8"))
    assert persisted["repo_path"] == str(repo_path.resolve())
    assert report["output_path"] == str(output_path.resolve())
