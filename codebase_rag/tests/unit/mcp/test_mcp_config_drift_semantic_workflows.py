from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from codebase_rag.mcp.tools import MCPToolsRegistry

pytestmark = [pytest.mark.anyio]


@pytest.fixture(params=["asyncio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text(
        "def read_env():\n    return True\n", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def mcp_registry(temp_repo: Path) -> MCPToolsRegistry:
    registry = MCPToolsRegistry(
        project_root=str(temp_repo),
        ingestor=MagicMock(),
        cypher_gen=MagicMock(),
    )
    registry._session_state["preflight_project_selected"] = True
    registry._session_state["preflight_schema_summary_loaded"] = True
    return registry


async def test_schema_overview_surfaces_config_drift_presets_and_next_calls(
    mcp_registry: MCPToolsRegistry,
) -> None:
    ingestor = cast(MagicMock, mcp_registry.ingestor)
    ingestor.fetch_all.side_effect = [
        [
            {
                "from_node_type": "Function",
                "relationship_type": "READS_ENV",
                "to_node_type": "EnvVar",
            },
            {
                "from_node_type": "InfraResource",
                "relationship_type": "SETS_ENV",
                "to_node_type": "EnvVar",
            },
            {
                "from_node_type": "Function",
                "relationship_type": "USES_SECRET",
                "to_node_type": "SecretRef",
            },
        ],
        [
            {"label": "EnvVar", "count": 4},
            {"label": "FeatureFlag", "count": 2},
            {"label": "SecretRef", "count": 2},
            {"label": "InfraResource", "count": 2},
        ],
    ]

    result = await mcp_registry.get_schema_overview(scope="api")

    assert result.get("status") == "ok"
    presets = cast(list[dict[str, object]], result.get("semantic_cypher_presets", []))
    preset_names = {str(item.get("name", "")) for item in presets}
    assert "unbound_secret_refs" in preset_names
    assert "orphan_feature_flags" in preset_names
    assert "resource_without_readers" in preset_names
    assert "reader_without_resource" in preset_names

    exact_next_calls = cast(list[dict[str, object]], result.get("exact_next_calls", []))
    assert any(
        "config/runtime drift" in str(item.get("when", "")).lower()
        or "config drift" in str(item.get("why", "")).lower()
        for item in exact_next_calls
    )
    assert any(
        str(item.get("tool", "")) == "run_cypher"
        and "InfraResource"
        in str(cast(dict[str, object], item.get("args", {})).get("cypher", ""))
        and "READS_ENV"
        in str(cast(dict[str, object], item.get("args", {})).get("cypher", ""))
        for item in exact_next_calls
    )
