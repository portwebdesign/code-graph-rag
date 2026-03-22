from __future__ import annotations

from pathlib import Path

from codebase_rag.core import constants as cs
from codebase_rag.tests.conftest import get_relationships, run_updater

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "python_map_dispatch"


def _write_fixture(project: Path, fixture_name: str) -> None:
    target = project / "dispatchers.py"
    target.write_text(
        (FIXTURE_DIR / fixture_name).read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def test_fixture_literal_dispatch_emits_single_dispatch_edge(
    temp_repo: Path,
    mock_ingestor,
) -> None:
    project = temp_repo / "fixture_literal_dispatch"
    project.mkdir()
    _write_fixture(project, "literal_dispatch.py")

    run_updater(project, mock_ingestor)

    dispatch_relationships = get_relationships(
        mock_ingestor,
        cs.RelationshipType.DISPATCHES_TO,
    )
    assert len(dispatch_relationships) == 1
    assert dispatch_relationships[0].args[3][cs.KEY_DISPATCH_KEY_KIND] == "literal"


def test_fixture_dynamic_dispatch_emits_all_callable_candidates(
    temp_repo: Path,
    mock_ingestor,
) -> None:
    project = temp_repo / "fixture_dynamic_dispatch"
    project.mkdir()
    _write_fixture(project, "dynamic_dispatch.py")

    run_updater(project, mock_ingestor)

    dispatch_relationships = get_relationships(
        mock_ingestor,
        cs.RelationshipType.DISPATCHES_TO,
    )
    assert {relationship.args[2][2] for relationship in dispatch_relationships} == {
        "fixture_dynamic_dispatch.dispatchers.handle_reclaim",
        "fixture_dynamic_dispatch.dispatchers.handle_status",
    }


def test_fixture_mixed_dispatch_ignores_non_callable_values(
    temp_repo: Path,
    mock_ingestor,
) -> None:
    project = temp_repo / "fixture_mixed_dispatch"
    project.mkdir()
    _write_fixture(project, "mixed_dispatch.py")

    run_updater(project, mock_ingestor)

    dispatch_relationships = get_relationships(
        mock_ingestor,
        cs.RelationshipType.DISPATCHES_TO,
    )
    assert len(dispatch_relationships) == 2
    assert all(
        "noop" not in relationship.args[2][2] for relationship in dispatch_relationships
    )


def test_fixture_control_case_fails_closed_for_non_literal_registry_builders(
    temp_repo: Path,
    mock_ingestor,
) -> None:
    project = temp_repo / "fixture_control_dispatch"
    project.mkdir()
    _write_fixture(project, "control_no_registry.py")

    run_updater(project, mock_ingestor)

    dispatch_relationships = get_relationships(
        mock_ingestor,
        cs.RelationshipType.DISPATCHES_TO,
    )
    assert dispatch_relationships == []
