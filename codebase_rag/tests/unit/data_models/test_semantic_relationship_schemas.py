from __future__ import annotations

from codebase_rag.core import constants as cs
from codebase_rag.data_models.types_defs import NODE_SCHEMAS, RELATIONSHIP_SCHEMAS


def test_semantic_node_schemas_are_registered() -> None:
    labels = {schema.label for schema in NODE_SCHEMAS}

    assert cs.NodeLabel.CONTRACT in labels
    assert cs.NodeLabel.CONTRACT_FIELD in labels
    assert cs.NodeLabel.DEPENDENCY_PROVIDER in labels
    assert cs.NodeLabel.AUTH_POLICY in labels
    assert cs.NodeLabel.AUTH_SCOPE in labels
    assert cs.NodeLabel.EVENT_FLOW in labels
    assert cs.NodeLabel.SQL_QUERY in labels
    assert cs.NodeLabel.CYPHER_QUERY in labels
    assert cs.NodeLabel.QUERY_FINGERPRINT in labels
    assert cs.NodeLabel.QUEUE in labels
    assert cs.NodeLabel.DATA_STORE in labels
    assert cs.NodeLabel.TRANSACTION_BOUNDARY in labels
    assert cs.NodeLabel.SIDE_EFFECT in labels
    assert cs.NodeLabel.RUNTIME_ARTIFACT in labels
    assert cs.NodeLabel.RUNTIME_EVENT in labels
    assert cs.NodeLabel.CLIENT_OPERATION in labels
    assert cs.NodeLabel.GRAPH_NODE_LABEL in labels
    assert cs.NodeLabel.TEST_SUITE in labels
    assert cs.NodeLabel.TEST_CASE in labels
    assert cs.NodeLabel.ENV_VAR in labels
    assert cs.NodeLabel.FEATURE_FLAG in labels
    assert cs.NodeLabel.SECRET_REF in labels


def test_fastapi_semantic_relationship_schemas_are_registered() -> None:
    schemas = {
        (schema.sources, schema.rel_type, schema.targets)
        for schema in RELATIONSHIP_SCHEMAS
    }

    assert (
        (cs.NodeLabel.ENDPOINT, cs.NodeLabel.FUNCTION, cs.NodeLabel.METHOD),
        cs.RelationshipType.USES_DEPENDENCY,
        (cs.NodeLabel.DEPENDENCY_PROVIDER,),
    ) in schemas
    assert (
        (cs.NodeLabel.ENDPOINT, cs.NodeLabel.FUNCTION, cs.NodeLabel.METHOD),
        cs.RelationshipType.SECURED_BY,
        (cs.NodeLabel.AUTH_POLICY,),
    ) in schemas
    assert (
        (cs.NodeLabel.AUTH_POLICY,),
        cs.RelationshipType.REQUIRES_SCOPE,
        (cs.NodeLabel.AUTH_SCOPE,),
    ) in schemas
    assert (
        (cs.NodeLabel.ENDPOINT, cs.NodeLabel.FUNCTION, cs.NodeLabel.METHOD),
        cs.RelationshipType.RETURNS_CONTRACT,
        (cs.NodeLabel.CONTRACT,),
    ) in schemas
    assert (
        (
            cs.NodeLabel.ENDPOINT,
            cs.NodeLabel.FUNCTION,
            cs.NodeLabel.METHOD,
            cs.NodeLabel.SERVICE,
        ),
        cs.RelationshipType.WRITES_OUTBOX,
        (cs.NodeLabel.EVENT_FLOW,),
    ) in schemas
    assert (
        (
            cs.NodeLabel.ENDPOINT,
            cs.NodeLabel.FUNCTION,
            cs.NodeLabel.METHOD,
            cs.NodeLabel.SERVICE,
        ),
        cs.RelationshipType.PUBLISHES_EVENT,
        (cs.NodeLabel.EVENT_FLOW,),
    ) in schemas
    assert (
        (
            cs.NodeLabel.MODULE,
            cs.NodeLabel.FUNCTION,
            cs.NodeLabel.METHOD,
            cs.NodeLabel.COMPONENT,
        ),
        cs.RelationshipType.EXECUTES_SQL,
        (cs.NodeLabel.SQL_QUERY,),
    ) in schemas
    assert (
        (
            cs.NodeLabel.MODULE,
            cs.NodeLabel.FUNCTION,
            cs.NodeLabel.METHOD,
            cs.NodeLabel.COMPONENT,
        ),
        cs.RelationshipType.EXECUTES_CYPHER,
        (cs.NodeLabel.CYPHER_QUERY,),
    ) in schemas
    assert (
        (cs.NodeLabel.SQL_QUERY, cs.NodeLabel.CYPHER_QUERY),
        cs.RelationshipType.HAS_FINGERPRINT,
        (cs.NodeLabel.QUERY_FINGERPRINT,),
    ) in schemas
    assert (
        (cs.NodeLabel.SQL_QUERY,),
        cs.RelationshipType.READS_TABLE,
        (cs.NodeLabel.DATA_STORE,),
    ) in schemas
    assert (
        (cs.NodeLabel.CYPHER_QUERY,),
        cs.RelationshipType.READS_LABEL,
        (cs.NodeLabel.GRAPH_NODE_LABEL,),
    ) in schemas
    assert (
        (
            cs.NodeLabel.MODULE,
            cs.NodeLabel.FUNCTION,
            cs.NodeLabel.METHOD,
            cs.NodeLabel.COMPONENT,
        ),
        cs.RelationshipType.USES_OPERATION,
        (cs.NodeLabel.CLIENT_OPERATION,),
    ) in schemas
    assert (
        (cs.NodeLabel.CLIENT_OPERATION,),
        cs.RelationshipType.REQUESTS_ENDPOINT,
        (cs.NodeLabel.ENDPOINT,),
    ) in schemas
    assert (
        (cs.NodeLabel.CLIENT_OPERATION,),
        cs.RelationshipType.BYPASSES_MANIFEST,
        (cs.NodeLabel.ENDPOINT,),
    ) in schemas
    assert (
        (cs.NodeLabel.FUNCTION, cs.NodeLabel.METHOD, cs.NodeLabel.SERVICE),
        cs.RelationshipType.CONSUMES_EVENT,
        (cs.NodeLabel.EVENT_FLOW,),
    ) in schemas
    assert (
        (cs.NodeLabel.FUNCTION, cs.NodeLabel.METHOD, cs.NodeLabel.SERVICE),
        cs.RelationshipType.REPLAYS_EVENT,
        (cs.NodeLabel.EVENT_FLOW,),
    ) in schemas
    assert (
        (cs.NodeLabel.FUNCTION, cs.NodeLabel.METHOD, cs.NodeLabel.SERVICE),
        cs.RelationshipType.WRITES_DLQ,
        (cs.NodeLabel.QUEUE,),
    ) in schemas
    assert (
        (cs.NodeLabel.FUNCTION, cs.NodeLabel.METHOD, cs.NodeLabel.SERVICE),
        cs.RelationshipType.BEGINS_TRANSACTION,
        (cs.NodeLabel.TRANSACTION_BOUNDARY,),
    ) in schemas
    assert (
        (cs.NodeLabel.FUNCTION, cs.NodeLabel.METHOD, cs.NodeLabel.SERVICE),
        cs.RelationshipType.COMMITS_TRANSACTION,
        (cs.NodeLabel.TRANSACTION_BOUNDARY,),
    ) in schemas
    assert (
        (cs.NodeLabel.FUNCTION, cs.NodeLabel.METHOD, cs.NodeLabel.SERVICE),
        cs.RelationshipType.ROLLBACKS_TRANSACTION,
        (cs.NodeLabel.TRANSACTION_BOUNDARY,),
    ) in schemas
    assert (
        (cs.NodeLabel.FUNCTION, cs.NodeLabel.METHOD, cs.NodeLabel.SERVICE),
        cs.RelationshipType.PERFORMS_SIDE_EFFECT,
        (cs.NodeLabel.SIDE_EFFECT,),
    ) in schemas
    assert (
        (cs.NodeLabel.SIDE_EFFECT,),
        cs.RelationshipType.WITHIN_TRANSACTION,
        (cs.NodeLabel.TRANSACTION_BOUNDARY,),
    ) in schemas
    assert (
        (cs.NodeLabel.SIDE_EFFECT,),
        cs.RelationshipType.BEFORE,
        (cs.NodeLabel.SIDE_EFFECT,),
    ) in schemas
    assert (
        (cs.NodeLabel.SIDE_EFFECT,),
        cs.RelationshipType.AFTER,
        (cs.NodeLabel.SIDE_EFFECT,),
    ) in schemas
    assert (
        (cs.NodeLabel.PROJECT, cs.NodeLabel.RUNTIME_ARTIFACT),
        cs.RelationshipType.CONTAINS,
        (cs.NodeLabel.RUNTIME_ARTIFACT, cs.NodeLabel.RUNTIME_EVENT),
    ) in schemas
    assert (
        (cs.NodeLabel.RUNTIME_EVENT,),
        cs.RelationshipType.OBSERVED_IN_RUNTIME,
        (
            cs.NodeLabel.ENDPOINT,
            cs.NodeLabel.DATA_STORE,
            cs.NodeLabel.CACHE_STORE,
            cs.NodeLabel.GRAPHQL_OPERATION,
            cs.NodeLabel.EVENT_FLOW,
            cs.NodeLabel.QUEUE,
            cs.NodeLabel.FUNCTION,
            cs.NodeLabel.METHOD,
        ),
    ) in schemas
    assert (
        (cs.NodeLabel.TEST_SUITE,),
        cs.RelationshipType.CONTAINS,
        (cs.NodeLabel.TEST_CASE,),
    ) in schemas
    assert (
        (cs.NodeLabel.TEST_CASE,),
        cs.RelationshipType.TESTS_SYMBOL,
        (
            cs.NodeLabel.MODULE,
            cs.NodeLabel.FUNCTION,
            cs.NodeLabel.METHOD,
            cs.NodeLabel.COMPONENT,
        ),
    ) in schemas
    assert (
        (cs.NodeLabel.TEST_CASE,),
        cs.RelationshipType.TESTS_ENDPOINT,
        (cs.NodeLabel.ENDPOINT,),
    ) in schemas
    assert (
        (cs.NodeLabel.TEST_CASE,),
        cs.RelationshipType.ASSERTS_CONTRACT,
        (cs.NodeLabel.CONTRACT,),
    ) in schemas
    assert (
        (
            cs.NodeLabel.MODULE,
            cs.NodeLabel.FUNCTION,
            cs.NodeLabel.METHOD,
            cs.NodeLabel.CLASS,
            cs.NodeLabel.COMPONENT,
            cs.NodeLabel.SERVICE,
            cs.NodeLabel.INFRA_RESOURCE,
        ),
        cs.RelationshipType.READS_ENV,
        (cs.NodeLabel.ENV_VAR,),
    ) in schemas
    assert (
        (cs.NodeLabel.INFRA_RESOURCE,),
        cs.RelationshipType.SETS_ENV,
        (cs.NodeLabel.ENV_VAR,),
    ) in schemas
    assert (
        (
            cs.NodeLabel.MODULE,
            cs.NodeLabel.FUNCTION,
            cs.NodeLabel.METHOD,
            cs.NodeLabel.CLASS,
            cs.NodeLabel.COMPONENT,
            cs.NodeLabel.SERVICE,
            cs.NodeLabel.INFRA_RESOURCE,
        ),
        cs.RelationshipType.USES_SECRET,
        (cs.NodeLabel.SECRET_REF,),
    ) in schemas
    assert (
        (cs.NodeLabel.FEATURE_FLAG,),
        cs.RelationshipType.GATES_CODE_PATH,
        (
            cs.NodeLabel.MODULE,
            cs.NodeLabel.FUNCTION,
            cs.NodeLabel.METHOD,
            cs.NodeLabel.CLASS,
            cs.NodeLabel.COMPONENT,
            cs.NodeLabel.SERVICE,
        ),
    ) in schemas
    assert (
        (
            cs.NodeLabel.ENDPOINT,
            cs.NodeLabel.DATA_STORE,
            cs.NodeLabel.CACHE_STORE,
            cs.NodeLabel.GRAPHQL_OPERATION,
            cs.NodeLabel.EVENT_FLOW,
            cs.NodeLabel.QUEUE,
            cs.NodeLabel.FUNCTION,
            cs.NodeLabel.METHOD,
        ),
        cs.RelationshipType.OBSERVED_IN_RUNTIME,
        (cs.NodeLabel.RUNTIME_EVENT,),
    ) in schemas
