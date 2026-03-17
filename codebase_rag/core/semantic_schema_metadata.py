from __future__ import annotations

from copy import deepcopy
from typing import Final

SEMANTIC_SCHEMA_VERSION: Final[str] = "1.0.0"

SEMANTIC_SIGNAL_PLANES: Final[list[dict[str, object]]] = [
    {
        "name": "static",
        "description": (
            "Parser-derived semantic edges extracted from repository source such as "
            "Python, TypeScript, OpenAPI, env/config files, and topology manifests."
        ),
        "trust_model": "grounded_in_repo_source",
    },
    {
        "name": "runtime",
        "description": (
            "Observed runtime artifacts such as event logs and coverage files that are "
            "attached additively and never overwrite static semantic nodes."
        ),
        "trust_model": "artifact_observed",
    },
    {
        "name": "heuristic",
        "description": (
            "Bounded lexical inference used when the graph needs higher-level semantic "
            "structure but no explicit framework contract exists."
        ),
        "trust_model": "bounded_inference",
    },
]

SEMANTIC_SCHEMA_COMPATIBILITY: Final[dict[str, object]] = {
    "breaking_change_policy": (
        "Bump the semantic schema major version when node labels, relationship names, "
        "or unique identity keys change incompatibly."
    ),
    "non_breaking_change_policy": (
        "Bump the minor version when new semantic capabilities, properties, or query "
        "presets are added without changing existing identities."
    ),
    "consumer_guidance": (
        "Prefer semantic_schema metadata from get_schema_overview(...) and the versioning "
        "document before relying on newly added semantic node families."
    ),
    "versioning_doc": "docs/architecture/semantic-schema-versioning.md",
    "release_closure_doc": "docs/architecture/semantic-release-closure.md",
}

SEMANTIC_SCHEMA_CAPABILITIES: Final[list[dict[str, object]]] = [
    {
        "id": "fastapi_auth_contract",
        "display_name": "FastAPI auth, dependency, and contract graph",
        "planes": ["static", "heuristic"],
        "docs_path": "docs/architecture/fastapi-semantic-graph.md",
        "node_labels": [
            "DependencyProvider",
            "AuthPolicy",
            "AuthScope",
            "Contract",
            "ContractField",
        ],
        "relationship_types": [
            "USES_DEPENDENCY",
            "SECURED_BY",
            "REQUIRES_SCOPE",
            "ACCEPTS_CONTRACT",
            "RETURNS_CONTRACT",
            "DECLARES_FIELD",
        ],
        "validation_queries": ["fastapi_auth_contract_minimum"],
        "query_presets": [
            "endpoint_auth_coverage",
            "endpoint_dependency_visibility",
            "endpoint_contract_gaps",
            "unprotected_endpoints",
        ],
    },
    {
        "id": "event_flow",
        "display_name": "Event flow, outbox, replay, and DLQ graph",
        "planes": ["static", "heuristic"],
        "docs_path": "docs/architecture/event-flow-semantic-graph.md",
        "node_labels": ["EventFlow", "Queue"],
        "relationship_types": [
            "WRITES_OUTBOX",
            "PUBLISHES_EVENT",
            "CONSUMES_EVENT",
            "WRITES_DLQ",
            "REPLAYS_EVENT",
            "USES_QUEUE",
            "USES_HANDLER",
        ],
        "validation_queries": ["event_flow_minimum"],
        "query_presets": [
            "outbox_without_transaction",
            "consumer_without_dlq",
            "replay_paths",
            "duplicate_publishers",
        ],
    },
    {
        "id": "runtime_reconciliation",
        "display_name": "Runtime artifact and static semantic reconciliation",
        "planes": ["runtime", "static", "heuristic"],
        "docs_path": "docs/architecture/event-flow-semantic-graph.md",
        "node_labels": ["RuntimeArtifact", "RuntimeEvent"],
        "relationship_types": ["OBSERVED_IN_RUNTIME", "CONTAINS", "COVERS_MODULE"],
        "validation_queries": [],
        "query_presets": [],
    },
    {
        "id": "transaction_flow",
        "display_name": "Transaction boundary and side-effect ordering graph",
        "planes": ["static", "heuristic"],
        "docs_path": "docs/architecture/transaction-semantic-graph.md",
        "node_labels": ["TransactionBoundary", "SideEffect"],
        "relationship_types": [
            "BEGINS_TRANSACTION",
            "COMMITS_TRANSACTION",
            "ROLLBACKS_TRANSACTION",
            "PERFORMS_SIDE_EFFECT",
            "WITHIN_TRANSACTION",
            "BEFORE",
            "AFTER",
        ],
        "validation_queries": ["transaction_flow_minimum"],
        "query_presets": [
            "outbox_without_transaction",
            "external_call_before_commit",
        ],
    },
    {
        "id": "query_fingerprint",
        "display_name": "SQL and Cypher query fingerprint graph",
        "planes": ["static", "heuristic"],
        "docs_path": "docs/architecture/query-fingerprint-semantic-graph.md",
        "node_labels": [
            "SqlQuery",
            "CypherQuery",
            "QueryFingerprint",
            "DataStore",
            "GraphNodeLabel",
        ],
        "relationship_types": [
            "EXECUTES_SQL",
            "EXECUTES_CYPHER",
            "HAS_FINGERPRINT",
            "READS_TABLE",
            "WRITES_TABLE",
            "READS_LABEL",
            "WRITES_LABEL",
            "JOINS_TABLE",
        ],
        "validation_queries": ["query_fingerprint_minimum"],
        "query_presets": [],
    },
    {
        "id": "frontend_operation_governance",
        "display_name": "Frontend operation governance graph",
        "planes": ["static", "heuristic"],
        "docs_path": "docs/architecture/frontend-operation-semantic-graph.md",
        "node_labels": ["ClientOperation", "Endpoint"],
        "relationship_types": [
            "USES_OPERATION",
            "REQUESTS_ENDPOINT",
            "GENERATED_FROM_SPEC",
            "BYPASSES_MANIFEST",
        ],
        "validation_queries": ["frontend_operation_minimum"],
        "query_presets": ["client_operations", "bypasses_manifest"],
    },
    {
        "id": "test_semantics",
        "display_name": "Testcase, endpoint, contract, and runtime coverage graph",
        "planes": ["static", "runtime", "heuristic"],
        "docs_path": "docs/architecture/test-semantic-graph.md",
        "node_labels": ["TestSuite", "TestCase", "Contract", "RuntimeEvent"],
        "relationship_types": [
            "TESTS_SYMBOL",
            "TESTS_ENDPOINT",
            "ASSERTS_CONTRACT",
            "COVERS_MODULE",
        ],
        "validation_queries": ["test_semantics_minimum"],
        "query_presets": ["untested_public_endpoints", "contract_test_coverage"],
    },
    {
        "id": "config_control_plane",
        "display_name": "Env, flag, secret, and infra control-plane graph",
        "planes": ["static", "heuristic"],
        "docs_path": "docs/architecture/config-semantic-graph.md",
        "node_labels": ["EnvVar", "FeatureFlag", "SecretRef", "InfraResource"],
        "relationship_types": [
            "READS_ENV",
            "SETS_ENV",
            "USES_SECRET",
            "GATES_CODE_PATH",
        ],
        "validation_queries": ["config_control_plane_minimum"],
        "query_presets": [
            "undefined_env_readers",
            "orphan_secret_refs",
            "unbound_secret_refs",
            "unused_feature_flags",
            "orphan_feature_flags",
            "resource_without_readers",
            "reader_without_resource",
        ],
    },
]

SEMANTIC_KNOWN_LIMITS: Final[list[str]] = [
    "Semantic passes intentionally use bounded heuristics instead of full control-flow or whole-program dataflow claims.",
    "Runtime evidence is additive and observational; it augments static graph planes but does not override source-derived identities.",
    "Frontend TypeScript semantic slices depend on parser availability; validation and smoke suites skip those slices when the parser is unavailable.",
    "High-cardinality semantic families are capped by central guardrails; the graph preserves representative evidence rather than unbounded exhaustiveness.",
]


def build_semantic_schema_metadata() -> dict[str, object]:
    return {
        "schema_version": SEMANTIC_SCHEMA_VERSION,
        "compatibility": deepcopy(SEMANTIC_SCHEMA_COMPATIBILITY),
        "signal_planes": deepcopy(SEMANTIC_SIGNAL_PLANES),
        "capabilities": deepcopy(SEMANTIC_SCHEMA_CAPABILITIES),
        "known_limits": list(SEMANTIC_KNOWN_LIMITS),
    }
