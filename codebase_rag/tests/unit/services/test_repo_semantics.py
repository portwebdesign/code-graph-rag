from __future__ import annotations

from pathlib import Path
from typing import cast

from codebase_rag.services.repo_semantics import RepoSemanticEnricher


def test_repo_semantics_detects_service_data_and_runtime_layers(tmp_path: Path) -> None:
    (tmp_path / "frontend").mkdir()
    (tmp_path / "backend").mkdir()
    (tmp_path / "output" / "runtime").mkdir(parents=True)

    (tmp_path / "frontend" / "App.tsx").write_text(
        "const query = gql`query Viewer { viewer { id } }`;",
        encoding="utf-8",
    )
    (tmp_path / "backend" / "api.py").write_text(
        '@app.get("/users")\nasync def users():\n    return {"ok": True}\n',
        encoding="utf-8",
    )
    (tmp_path / "schema.graphql").write_text(
        "type Query { viewer: User }\n",
        encoding="utf-8",
    )
    (tmp_path / "docker-compose.yml").write_text(
        """
services:
  api:
    image: my-api
    depends_on:
      - redis
      - memgraph
  redis:
    image: redis:7
  memgraph:
    image: memgraph/memgraph
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
dependencies = [
  "fastapi>=0.110.0",
  "redis>=5.0.0",
  "memgraph>=1.0.0",
  "graphql-core>=3.2.0",
]
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "output" / "runtime" / "trace.json").write_text(
        '{"kind":"http","route_path":"/users"}',
        encoding="utf-8",
    )

    result = RepoSemanticEnricher().summarize(tmp_path, max_files=80)
    api_styles = cast(list[object], result["api_styles"])
    data_systems = cast(dict[str, object], result["data_systems"])
    runtime_signals = cast(dict[str, object], result["runtime_signals"])
    services = cast(list[object], result["services"])

    assert "rest" in api_styles
    assert "graphql" in api_styles
    assert "memgraph" in cast(list[object], data_systems["datastores"])
    assert "redis" in cast(list[object], data_systems["caches"])
    assert runtime_signals["dynamic_analysis_present"] is True
    assert "frontend" in services
    assert "backend" in services
    assert "Data stores:" in str(result["summary"])
