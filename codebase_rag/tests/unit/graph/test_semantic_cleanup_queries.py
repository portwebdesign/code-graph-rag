from codebase_rag.graph_db.cypher_queries import (
    CYPHER_DELETE_DYNAMIC_EDGES_BY_PATH,
    CYPHER_DELETE_MODULE_BY_PATH,
)


def test_module_delete_query_cleans_semantic_edges_and_orphans() -> None:
    assert "WHERE r.path = path" in CYPHER_DELETE_MODULE_BY_PATH
    assert "n:ContractField" in CYPHER_DELETE_MODULE_BY_PATH
    assert "n:EventFlow" in CYPHER_DELETE_MODULE_BY_PATH
    assert "n:TransactionBoundary" in CYPHER_DELETE_MODULE_BY_PATH
    assert "n:SideEffect" in CYPHER_DELETE_MODULE_BY_PATH
    assert "n:DependencyProvider" in CYPHER_DELETE_MODULE_BY_PATH
    assert "n:EnvVar" in CYPHER_DELETE_MODULE_BY_PATH
    assert "n:FeatureFlag" in CYPHER_DELETE_MODULE_BY_PATH
    assert "n:SecretRef" in CYPHER_DELETE_MODULE_BY_PATH
    assert "WRITES_OUTBOX" in CYPHER_DELETE_MODULE_BY_PATH
    assert "PERFORMS_SIDE_EFFECT" in CYPHER_DELETE_MODULE_BY_PATH
    assert "WITHIN_TRANSACTION" in CYPHER_DELETE_MODULE_BY_PATH
    assert "READS_ENV" in CYPHER_DELETE_MODULE_BY_PATH
    assert "SETS_ENV" in CYPHER_DELETE_MODULE_BY_PATH
    assert "USES_SECRET" in CYPHER_DELETE_MODULE_BY_PATH
    assert "GATES_CODE_PATH" in CYPHER_DELETE_MODULE_BY_PATH
    assert (
        "MATCH (artifact:RuntimeArtifact {path: path})" in CYPHER_DELETE_MODULE_BY_PATH
    )
    assert (
        "OPTIONAL MATCH (artifact)-[:CONTAINS]->(event:RuntimeEvent)"
        in CYPHER_DELETE_MODULE_BY_PATH
    )
    assert "NOT (n)--()" in CYPHER_DELETE_MODULE_BY_PATH


def test_dynamic_edge_delete_query_cleans_semantic_edges_and_orphans() -> None:
    assert "WHERE r.path = path" in CYPHER_DELETE_DYNAMIC_EDGES_BY_PATH
    assert "DECLARES_FIELD" in CYPHER_DELETE_DYNAMIC_EDGES_BY_PATH
    assert "n:AuthPolicy" in CYPHER_DELETE_DYNAMIC_EDGES_BY_PATH
    assert "WRITES_DLQ" in CYPHER_DELETE_DYNAMIC_EDGES_BY_PATH
    assert "REPLAYS_EVENT" in CYPHER_DELETE_DYNAMIC_EDGES_BY_PATH
    assert "BEGINS_TRANSACTION" in CYPHER_DELETE_DYNAMIC_EDGES_BY_PATH
    assert "AFTER" in CYPHER_DELETE_DYNAMIC_EDGES_BY_PATH
    assert "READS_ENV" in CYPHER_DELETE_DYNAMIC_EDGES_BY_PATH
    assert "SETS_ENV" in CYPHER_DELETE_DYNAMIC_EDGES_BY_PATH
    assert "USES_SECRET" in CYPHER_DELETE_DYNAMIC_EDGES_BY_PATH
    assert "GATES_CODE_PATH" in CYPHER_DELETE_DYNAMIC_EDGES_BY_PATH
    assert (
        "MATCH (artifact:RuntimeArtifact {path: path})"
        in CYPHER_DELETE_DYNAMIC_EDGES_BY_PATH
    )
    assert (
        "OPTIONAL MATCH (artifact)-[:CONTAINS]->(event:RuntimeEvent)"
        in CYPHER_DELETE_DYNAMIC_EDGES_BY_PATH
    )
    assert "NOT (n)--()" in CYPHER_DELETE_DYNAMIC_EDGES_BY_PATH
