from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

from codebase_rag.core import constants as cs
from codebase_rag.graph_db.graph_updater import GraphUpdater
from codebase_rag.infrastructure.parser_loader import load_parsers


@dataclass(frozen=True)
class SemanticFixtureSpec:
    """Defines a tiny repository fixture used for semantic graph regression tests."""

    name: str
    files: dict[str, str]


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def materialize_fixture_repo(base_dir: Path, spec: SemanticFixtureSpec) -> Path:
    """Creates a fixture repository on disk and returns its root path."""

    repo_path = base_dir / spec.name
    repo_path.mkdir(parents=True, exist_ok=True)
    for relative_path, content in spec.files.items():
        _write_file(repo_path / relative_path, content)
    return repo_path


def run_fixture_update(repo_path: Path, ingestor: object) -> None:
    """Runs the graph updater in full-reparse mode for deterministic fixtures."""

    parsers, queries = load_parsers()
    updater = GraphUpdater(
        ingestor=cast(Any, ingestor),
        repo_path=repo_path,
        parsers=parsers,
        queries=queries,
        force_full_reparse=True,
    )
    updater.run()


def _normalize(value: object) -> object:
    if hasattr(value, "value"):
        value = cast(Any, value).value
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        return {
            str(key): _normalize(val)
            for key, val in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, tuple):
        return [_normalize(item) for item in value]
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def build_mock_graph_snapshot(
    mock_ingestor: MagicMock,
    *,
    node_labels: set[str] | None = None,
    relationship_types: set[str] | None = None,
) -> dict[str, list[dict[str, object]]]:
    """Builds a deterministic semantic subgraph snapshot from mock ingestor calls."""

    nodes: list[dict[str, object]] = []
    relationships: list[dict[str, object]] = []

    for call in mock_ingestor.ensure_node_batch.call_args_list:
        label = str(_normalize(call.args[0]))
        if node_labels and label not in node_labels:
            continue
        props = cast(dict[str, object], call.args[1])
        identity_key = str(cs.NODE_UNIQUE_CONSTRAINTS.get(label, cs.KEY_NAME))
        identity_value = props.get(identity_key)
        nodes.append(
            {
                "label": label,
                "identity_key": identity_key,
                "identity_value": _normalize(identity_value),
                "props": _normalize(props),
            }
        )

    for call in mock_ingestor.ensure_relationship_batch.call_args_list:
        source_spec = cast(tuple[object, object, object], call.args[0])
        relationship_type = str(_normalize(call.args[1]))
        if relationship_types and relationship_type not in relationship_types:
            continue
        target_spec = cast(tuple[object, object, object], call.args[2])
        properties = (
            cast(dict[str, object], call.args[3])
            if len(call.args) > 3 and isinstance(call.args[3], dict)
            else {}
        )
        relationships.append(
            {
                "relationship_type": relationship_type,
                "from": {
                    "label": str(_normalize(source_spec[0])),
                    "key": str(_normalize(source_spec[1])),
                    "value": _normalize(source_spec[2]),
                },
                "to": {
                    "label": str(_normalize(target_spec[0])),
                    "key": str(_normalize(target_spec[1])),
                    "value": _normalize(target_spec[2]),
                },
                "props": _normalize(properties),
            }
        )

    nodes.sort(
        key=lambda item: (
            str(item["label"]),
            str(item["identity_value"]),
        )
    )
    relationships.sort(
        key=lambda item: (
            str(item["relationship_type"]),
            str(cast(dict[str, object], item["from"])["value"]),
            str(cast(dict[str, object], item["to"])["value"]),
        )
    )

    return {"nodes": nodes, "relationships": relationships}


def export_project_semantic_snapshot(
    ingestor: Any,
    *,
    project_name: str,
    node_labels: tuple[str, ...] = (),
    relationship_types: tuple[str, ...] = (),
) -> dict[str, list[dict[str, object]]]:
    """Exports a deterministic semantic subgraph snapshot from Memgraph."""

    node_rows = cast(
        "list[dict[str, object]]",
        ingestor._execute_query(
            """
MATCH (n {project_name: $project_name})
WHERE size($node_labels) = 0 OR any(label IN labels(n) WHERE label IN $node_labels)
RETURN labels(n) AS labels,
       properties(n) AS props,
       coalesce(n.qualified_name, n.name, n.path, '') AS identity
ORDER BY identity
""",
            {"project_name": project_name, "node_labels": list(node_labels)},
        ),
    )
    relationship_rows = cast(
        "list[dict[str, object]]",
        ingestor._execute_query(
            """
MATCH (a {project_name: $project_name})-[r]->(b {project_name: $project_name})
WHERE size($relationship_types) = 0 OR type(r) IN $relationship_types
RETURN labels(a) AS from_labels,
       properties(a) AS from_props,
       type(r) AS rel_type,
       properties(r) AS rel_props,
       labels(b) AS to_labels,
       properties(b) AS to_props,
       coalesce(a.qualified_name, a.name, a.path, '') AS from_identity,
       coalesce(b.qualified_name, b.name, b.path, '') AS to_identity
ORDER BY rel_type, from_identity, to_identity
""",
            {
                "project_name": project_name,
                "relationship_types": list(relationship_types),
            },
        ),
    )

    nodes = [
        {
            "labels": sorted(cast(list[str], _normalize(row["labels"]))),
            "identity": row["identity"],
            "props": _normalize(row["props"]),
        }
        for row in node_rows
    ]
    relationships = [
        {
            "relationship_type": row["rel_type"],
            "from_labels": sorted(cast(list[str], _normalize(row["from_labels"]))),
            "from_identity": row["from_identity"],
            "to_labels": sorted(cast(list[str], _normalize(row["to_labels"]))),
            "to_identity": row["to_identity"],
            "props": _normalize(row["rel_props"]),
        }
        for row in relationship_rows
    ]
    return {"nodes": nodes, "relationships": relationships}


def execute_project_cypher(
    ingestor: Any,
    query: str,
    *,
    project_name: str,
    parameters: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    """Runs a project-scoped Cypher query against the current Memgraph fixture graph."""

    query_params = {cs.KEY_PROJECT_NAME: project_name}
    if parameters:
        query_params.update(parameters)

    return cast(
        "list[dict[str, object]]",
        ingestor._execute_query(query, query_params),
    )
