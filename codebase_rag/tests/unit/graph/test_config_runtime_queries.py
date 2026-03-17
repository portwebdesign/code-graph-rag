from codebase_rag.graph_db.cypher_queries import (
    CYPHER_CONFIG_ORPHAN_FEATURE_FLAGS,
    CYPHER_CONFIG_ORPHAN_SECRET_REFS,
    CYPHER_CONFIG_READER_WITHOUT_RESOURCE,
    CYPHER_CONFIG_RESOURCE_WITHOUT_READERS,
    CYPHER_CONFIG_UNDEFINED_ENV_READERS,
    CYPHER_CONFIG_UNUSED_FEATURE_FLAGS,
    build_config_runtime_query_pack,
)


def test_config_runtime_query_pack_is_project_scoped() -> None:
    query_pack = build_config_runtime_query_pack()

    assert len(query_pack) == 7
    names = {entry["name"] for entry in query_pack}
    assert "unbound_secret_refs" in names
    assert "orphan_feature_flags" in names
    assert "resource_without_readers" in names
    assert "reader_without_resource" in names
    for entry in query_pack:
        cypher = entry["cypher"]
        assert "$project_name" in cypher
        assert "project_name: $project_name" in cypher


def test_config_runtime_queries_use_expected_relationships() -> None:
    assert "READS_ENV" in CYPHER_CONFIG_UNDEFINED_ENV_READERS
    assert "SETS_ENV" in CYPHER_CONFIG_UNDEFINED_ENV_READERS
    assert "USES_SECRET" in CYPHER_CONFIG_ORPHAN_SECRET_REFS
    assert "GATES_CODE_PATH" in CYPHER_CONFIG_UNUSED_FEATURE_FLAGS
    assert "GATES_CODE_PATH" in CYPHER_CONFIG_ORPHAN_FEATURE_FLAGS
    assert "SETS_ENV" in CYPHER_CONFIG_RESOURCE_WITHOUT_READERS
    assert "READS_ENV" in CYPHER_CONFIG_RESOURCE_WITHOUT_READERS
    assert "READS_ENV" in CYPHER_CONFIG_READER_WITHOUT_RESOURCE
    assert "SETS_ENV" in CYPHER_CONFIG_READER_WITHOUT_RESOURCE
