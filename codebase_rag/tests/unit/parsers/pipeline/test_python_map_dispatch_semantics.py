from __future__ import annotations

from pathlib import Path

import pytest

from codebase_rag.core import constants as cs
from codebase_rag.tests.conftest import get_relationships, run_updater


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_python_literal_dispatch_emits_precise_dispatch_edge(
    temp_repo: Path,
    mock_ingestor,
) -> None:
    project = temp_repo / "python_literal_dispatch"
    project.mkdir()

    _write(
        project / "dispatchers.py",
        """def handle_status() -> str:
    return \"ok\"


def handle_reclaim() -> str:
    return \"reclaimed\"


def run(command: str) -> str:
    handlers = {
        \"status\": handle_status,
        \"reclaim\": handle_reclaim,
    }
    return handlers[\"status\"]()
""",
    )

    run_updater(project, mock_ingestor)

    dispatch_relationships = get_relationships(
        mock_ingestor,
        cs.RelationshipType.DISPATCHES_TO,
    )
    assert len(dispatch_relationships) == 1

    relationship = dispatch_relationships[0]
    assert relationship.args[0] == (
        cs.NodeLabel.FUNCTION,
        cs.KEY_QUALIFIED_NAME,
        "python_literal_dispatch.dispatchers.run",
    )
    assert relationship.args[2] == (
        cs.NodeLabel.FUNCTION,
        cs.KEY_QUALIFIED_NAME,
        "python_literal_dispatch.dispatchers.handle_status",
    )
    assert relationship.args[3][cs.KEY_DISPATCH_REGISTRY] == "handlers"
    assert relationship.args[3][cs.KEY_DISPATCH_KEY] == "status"
    assert relationship.args[3][cs.KEY_DISPATCH_KEY_KIND] == "literal"
    assert relationship.args[3][cs.KEY_EVIDENCE_KIND] == "python_map_dispatch"
    assert relationship.args[3][cs.KEY_RELATION_TYPE] == "dispatch"
    assert relationship.args[3][cs.KEY_CONFIDENCE] == pytest.approx(0.98)


def test_python_dynamic_dispatch_emits_edges_for_registry_candidates(
    temp_repo: Path,
    mock_ingestor,
) -> None:
    project = temp_repo / "python_dynamic_dispatch"
    project.mkdir()

    _write(
        project / "dispatchers.py",
        """def handle_status() -> str:
    return \"ok\"


def handle_reclaim() -> str:
    return \"reclaimed\"


def run(command: str) -> str:
    handlers = {
        \"status\": handle_status,
        \"reclaim\": handle_reclaim,
    }
    return handlers[command]()
""",
    )

    run_updater(project, mock_ingestor)

    dispatch_relationships = get_relationships(
        mock_ingestor,
        cs.RelationshipType.DISPATCHES_TO,
    )
    targets = {
        relationship.args[2][2]: relationship.args[3]
        for relationship in dispatch_relationships
    }

    assert set(targets) == {
        "python_dynamic_dispatch.dispatchers.handle_reclaim",
        "python_dynamic_dispatch.dispatchers.handle_status",
    }
    for props in targets.values():
        assert props[cs.KEY_DISPATCH_REGISTRY] == "handlers"
        assert props[cs.KEY_DISPATCH_KEY] == "command"
        assert props[cs.KEY_DISPATCH_KEY_KIND] == "identifier"
        assert props[cs.KEY_EVIDENCE_KIND] == "python_map_dispatch"
        assert props[cs.KEY_RELATION_TYPE] == "dispatch"
        assert props[cs.KEY_CONFIDENCE] == pytest.approx(0.72)
