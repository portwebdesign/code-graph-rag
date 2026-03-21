from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from unittest.mock import MagicMock

from codebase_rag.core import constants as cs
from codebase_rag.data_models.types_defs import NodeType
from codebase_rag.parsers.frameworks.framework_linker import (
    EndpointMatch,
    FrameworkLinker,
)
from codebase_rag.tests.conftest import get_relationships


def test_non_controller_endpoints_emit_routes_to_action(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = temp_repo / "route_action_linking"
    project.mkdir()
    source_file = project / "main.go"
    source_file.write_text("package main\n", encoding="utf-8")

    function_registry = MagicMock()
    function_registry.find_ending_with.side_effect = (
        lambda suffix: ["route_action_linking.main.healthHandler"]
        if suffix == "healthHandler"
        else []
    )
    function_registry.get.side_effect = (
        lambda qn, default=None: NodeType.FUNCTION
        if qn == "route_action_linking.main.healthHandler"
        else default
    )

    linker = FrameworkLinker(
        repo_path=project,
        project_name="route_action_linking",
        ingestor=mock_ingestor,
        function_registry=function_registry,
        simple_name_lookup=defaultdict(set),
    )
    linker._link_endpoints(
        source_file,
        [
            EndpointMatch(
                framework="go_web",
                method="GET",
                path="/health",
                handler_name="healthHandler",
            )
        ],
    )

    endpoint_qn = "route_action_linking.endpoint.go_web.GET:/health"
    has_endpoint = [
        call.args
        for call in get_relationships(mock_ingestor, cs.RelationshipType.HAS_ENDPOINT)
    ]
    routes_to_action = [
        call.args
        for call in get_relationships(
            mock_ingestor, cs.RelationshipType.ROUTES_TO_ACTION
        )
    ]

    assert any(
        rel[2] == (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn)
        and str(rel[0][2]).endswith(".healthHandler")
        for rel in has_endpoint
    )
    assert any(
        rel[0] == (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn)
        and str(rel[2][2]).endswith(".healthHandler")
        for rel in routes_to_action
    )
