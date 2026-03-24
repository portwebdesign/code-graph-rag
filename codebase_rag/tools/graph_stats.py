from __future__ import annotations

from codebase_rag.services import QueryProtocol


def get_graph_stats(ingestor: QueryProtocol) -> dict[str, object]:
    node_count = ingestor.fetch_all("MATCH (n) RETURN count(n) AS count")
    rel_count = ingestor.fetch_all("MATCH ()-[r]->() RETURN count(r) AS count")
    label_stats = ingestor.fetch_all(
        "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS count ORDER BY count DESC"
    )
    rel_stats = ingestor.fetch_all(
        "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS count ORDER BY count DESC"
    )
    return {
        "nodes": node_count[0]["count"] if node_count else 0,
        "relationships": rel_count[0]["count"] if rel_count else 0,
        "labels": label_stats,
        "relationship_types": rel_stats,
    }


def get_dependency_stats(ingestor: QueryProtocol) -> dict[str, object]:
    total = ingestor.fetch_all(
        "MATCH (m:Module)-[:DEFINES]->(i:Import) RETURN count(i) AS count"
    )
    top_importers = ingestor.fetch_all(
        "MATCH (m:Module)-[:DEFINES]->(i:Import) "
        "RETURN m.qualified_name AS module, count(i) AS count "
        "ORDER BY count DESC LIMIT 10"
    )
    top_dependents = ingestor.fetch_all(
        "MATCH (m:Module)-[:DEFINES]->(i:Import) "
        "RETURN i.import_source AS target, count(*) AS count "
        "ORDER BY count DESC LIMIT 10"
    )
    return {
        "total_imports": total[0]["count"] if total else 0,
        "top_importers": top_importers,
        "top_dependents": top_dependents,
    }
