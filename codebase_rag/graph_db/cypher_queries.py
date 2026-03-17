from codebase_rag.core.constants import CYPHER_DEFAULT_LIMIT

CYPHER_DELETE_ALL = "MATCH (n) DETACH DELETE n;"
"""Deletes all nodes and relationships from the database."""

CYPHER_LIST_PROJECTS = "MATCH (p:Project) RETURN p.name AS name ORDER BY p.name"
"""Lists the names of all projects in the database."""

CYPHER_DELETE_PROJECT = """
MATCH (p:Project {name: $project_name})
OPTIONAL MATCH (p)-[:CONTAINS_PACKAGE|CONTAINS_FOLDER|CONTAINS_FILE|CONTAINS_MODULE*]->(container)
OPTIONAL MATCH (container)-[:DEFINES|DEFINES_METHOD*]->(defined)
DETACH DELETE p, container, defined
"""
"""Deletes a specific project and all its associated nodes and relationships."""

CYPHER_DELETE_ANALYSIS_REPORTS = """
MATCH (p:Project {name: $project_name})-[:HAS_ANALYSIS]->(r:AnalysisReport)
OPTIONAL MATCH (r)-[:HAS_METRIC]->(m:AnalysisMetric)
DETACH DELETE m, r
"""

CYPHER_DELETE_ANALYSIS_RUNS = """
MATCH (p:Project {name: $project_name})-[:HAS_RUN]->(run:AnalysisRun)
OPTIONAL MATCH (run)-[:HAS_ANALYSIS]->(r:AnalysisReport)
OPTIONAL MATCH (r)-[:HAS_METRIC]->(m:AnalysisMetric)
DETACH DELETE m, r, run
"""

CYPHER_GET_LATEST_ANALYSIS_RUN = """
MATCH (p:Project {name: $project_name})-[:HAS_RUN]->(run:AnalysisRun)
RETURN run.analysis_timestamp AS analysis_timestamp, run.run_id AS run_id
ORDER BY run.analysis_timestamp DESC
LIMIT 1
"""

CYPHER_GET_LATEST_GIT_HEAD = """
MATCH (p:Project {name: $project_name})-[:HAS_RUN]->(run:AnalysisRun)
RETURN run.git_head AS git_head
ORDER BY run.analysis_timestamp DESC
LIMIT 1
"""

CYPHER_GET_LATEST_ANALYSIS_REPORT = """
MATCH (p:Project {name: $project_name})-[:HAS_ANALYSIS]->(r:AnalysisReport)
RETURN r.analysis_summary AS analysis_summary,
       r.analysis_timestamp AS analysis_timestamp,
       r.analysis_run_id AS run_id
ORDER BY r.analysis_timestamp DESC
LIMIT 1
"""

CYPHER_GET_LATEST_ANALYSIS_REPORTS = """
MATCH (p:Project)-[:HAS_ANALYSIS]->(r:AnalysisReport)
WITH p, r ORDER BY r.analysis_timestamp DESC
WITH p, collect(r)[0] AS latest
RETURN p.name AS project_name,
       latest.analysis_timestamp AS analysis_timestamp,
       latest.analysis_summary AS analysis_summary,
       latest.analysis_run_id AS run_id
ORDER BY project_name
"""

CYPHER_GET_LATEST_METRIC = """
MATCH (p:Project {name: $project_name})-[:HAS_RUN]->(run:AnalysisRun)
MATCH (run)-[:HAS_ANALYSIS]->(r:AnalysisReport)-[:HAS_METRIC]->(m:AnalysisMetric)
WHERE m.metric_name = $metric_name
RETURN m.metric_value AS metric_value, run.analysis_timestamp AS analysis_timestamp
ORDER BY run.analysis_timestamp DESC
LIMIT 1
"""

CYPHER_GET_METRIC_TIMELINE = """
MATCH (p:Project {name: $project_name})-[:HAS_RUN]->(run:AnalysisRun)
MATCH (run)-[:HAS_ANALYSIS]->(r:AnalysisReport)-[:HAS_METRIC]->(m:AnalysisMetric)
WHERE m.metric_name = $metric_name
RETURN m.metric_name AS metric_name,
       run.analysis_timestamp AS analysis_timestamp,
       m.metric_value AS metric_value,
       run.run_id AS run_id
ORDER BY run.analysis_timestamp DESC
LIMIT $limit
"""

CYPHER_BACKFILL_ANALYSIS_PROJECTS = """
MATCH (r:AnalysisReport)
WHERE r.project_name IS NOT NULL
MERGE (p:Project {name: r.project_name})
MERGE (p)-[:HAS_ANALYSIS]->(r)
"""

CYPHER_BACKFILL_ANALYSIS_RUNS = """
MATCH (run:AnalysisRun)
WHERE run.project_name IS NOT NULL
MERGE (p:Project {name: run.project_name})
MERGE (p)-[:HAS_RUN]->(run)
"""

CYPHER_BACKFILL_ANALYSIS_RUN_REPORT = """
MATCH (run:AnalysisRun)
WHERE run.analysis_run_id IS NOT NULL
MATCH (r:AnalysisReport {analysis_run_id: run.analysis_run_id, project_name: run.project_name})
MERGE (run)-[:HAS_ANALYSIS]->(r)
"""

CYPHER_BACKFILL_ANALYSIS_METRICS = """
MATCH (m:AnalysisMetric)
WHERE m.project_name IS NOT NULL
MATCH (r:AnalysisReport {analysis_run_id: m.analysis_run_id, project_name: m.project_name})
MERGE (r)-[:HAS_METRIC]->(m)
"""

CYPHER_BACKFILL_DEAD_CODE_NODE_CACHE_DEFAULTS = """
MATCH (f)
WHERE f:Function OR f:Method
SET f.in_call_count = coalesce(f.in_call_count, 0),
    f.out_call_count = coalesce(f.out_call_count, 0),
    f.is_reachable = coalesce(f.is_reachable, true),
    f.reachability_source = coalesce(f.reachability_source, 'legacy_unknown'),
    f.analysis_run_id = coalesce(f.analysis_run_id, 'legacy_backfill'),
    f.dead_code_score = coalesce(f.dead_code_score, 0)
RETURN count(f) AS updated
"""

CYPHER_BACKFILL_RELATION_METADATA_DEFAULTS = """
MATCH ()-[r]->()
SET r.source_parser = coalesce(r.source_parser, 'legacy_backfill'),
    r.analysis_run_id = coalesce(r.analysis_run_id, 'legacy_backfill'),
    r.last_seen_at = coalesce(r.last_seen_at, datetime())
RETURN count(r) AS updated
"""

CYPHER_DELETE_MODULE_BY_PATH = """
MATCH (m:Module {path: $path})
OPTIONAL MATCH (m)-[:DEFINES|DEFINES_METHOD*0..5]->(defined)
DETACH DELETE defined
DETACH DELETE m
WITH $path AS path
MATCH (f:File {path: path})
DETACH DELETE f
WITH path
MATCH ()-[r:USES_DEPENDENCY|SECURED_BY|REQUIRES_SCOPE|ACCEPTS_CONTRACT|RETURNS_CONTRACT|DECLARES_FIELD|WRITES_OUTBOX|PUBLISHES_EVENT|CONSUMES_EVENT|WRITES_DLQ|REPLAYS_EVENT|USES_QUEUE|USES_HANDLER|BEGINS_TRANSACTION|COMMITS_TRANSACTION|ROLLBACKS_TRANSACTION|PERFORMS_SIDE_EFFECT|WITHIN_TRANSACTION|BEFORE|AFTER|EXECUTES_SQL|EXECUTES_CYPHER|HAS_FINGERPRINT|READS_TABLE|WRITES_TABLE|READS_LABEL|WRITES_LABEL|JOINS_TABLE|USES_OPERATION|GENERATED_FROM_SPEC|BYPASSES_MANIFEST|TESTS_SYMBOL|TESTS_ENDPOINT|ASSERTS_CONTRACT|READS_ENV|SETS_ENV|USES_SECRET|GATES_CODE_PATH|CONTAINS]->()
WHERE r.path = path
DELETE r
WITH path
MATCH (artifact:RuntimeArtifact {path: path})
OPTIONAL MATCH (artifact)-[:CONTAINS]->(event:RuntimeEvent)
DETACH DELETE event, artifact
WITH 1 AS _
MATCH (n)
WHERE (
  n:Contract
  OR n:ContractField
  OR n:DependencyProvider
  OR n:AuthPolicy
  OR n:AuthScope
  OR n:EventFlow
  OR n:SqlQuery
  OR n:CypherQuery
  OR n:QueryFingerprint
  OR n:TransactionBoundary
  OR n:SideEffect
  OR n:ClientOperation
  OR n:TestSuite
  OR n:TestCase
  OR n:EnvVar
  OR n:FeatureFlag
  OR n:SecretRef
  OR (n:Queue AND coalesce(n.source_parser, '') = 'event_flow_pass')
  OR (n:DataStore AND coalesce(n.source_parser, '') = 'query_fingerprint_pass')
  OR (n:GraphNodeLabel AND coalesce(n.source_parser, '') = 'query_fingerprint_pass')
  OR (n:Endpoint AND coalesce(n.source_parser, '') = 'frontend_operation_pass')
)
  AND NOT (n)--()
DETACH DELETE n
"""

CYPHER_DELETE_DYNAMIC_EDGES_BY_PATH = """
MATCH (m:Module {path: $path})
OPTIONAL MATCH (m)-[:DEFINES|DEFINES_METHOD*0..5]->(defined)
OPTIONAL MATCH (defined)-[:HAS_ENDPOINT]->(endpoint)
WITH collect(DISTINCT m) + collect(DISTINCT defined) + collect(DISTINCT endpoint) AS nodes
UNWIND nodes AS node
MATCH (node)-[r:CALLS|IMPORTS|EXPORTS|EXPORTS_MODULE|IMPLEMENTS_MODULE|INHERITS|IMPLEMENTS|OVERRIDES|RETURNS_TYPE|PARAMETER_TYPE|CAUGHT_BY|THROWS|DECORATES|ANNOTATES|REQUIRES_LIBRARY|DEPENDS_ON|DEPENDS_ON_EXTERNAL|HAS_ENDPOINT|ROUTES_TO_CONTROLLER|ROUTES_TO_ACTION|RENDERS_VIEW|USES_MIDDLEWARE|REGISTERS_SERVICE|ELOQUENT_RELATION|HOOKS|REGISTERS_BLOCK|USES_ASSET|USES_UTILITY|RESOLVES_IMPORT|USES_COMPONENT|HANDLES_ERROR|MUTATES_STATE|HAS_PARAMETER|HAS_TYPE_PARAMETER|EMBEDS|REQUESTS_ENDPOINT|USES_OPERATION|USES_DEPENDENCY|SECURED_BY|REQUIRES_SCOPE|ACCEPTS_CONTRACT|RETURNS_CONTRACT|DECLARES_FIELD|USES_HANDLER|USES_SERVICE|PROVIDES_SERVICE|WRITES_OUTBOX|PUBLISHES_EVENT|CONSUMES_EVENT|WRITES_DLQ|REPLAYS_EVENT|BEGINS_TRANSACTION|COMMITS_TRANSACTION|ROLLBACKS_TRANSACTION|PERFORMS_SIDE_EFFECT|WITHIN_TRANSACTION|BEFORE|AFTER|EXECUTES_SQL|EXECUTES_CYPHER|HAS_FINGERPRINT|READS_TABLE|WRITES_TABLE|READS_LABEL|WRITES_LABEL|JOINS_TABLE|GENERATED_FROM_SPEC|BYPASSES_MANIFEST|TESTS_SYMBOL|TESTS_ENDPOINT|ASSERTS_CONTRACT|READS_ENV|SETS_ENV|USES_SECRET|GATES_CODE_PATH]-()
DELETE r
WITH $path AS path
MATCH ()-[r:USES_DEPENDENCY|SECURED_BY|REQUIRES_SCOPE|ACCEPTS_CONTRACT|RETURNS_CONTRACT|DECLARES_FIELD|WRITES_OUTBOX|PUBLISHES_EVENT|CONSUMES_EVENT|WRITES_DLQ|REPLAYS_EVENT|USES_QUEUE|USES_HANDLER|BEGINS_TRANSACTION|COMMITS_TRANSACTION|ROLLBACKS_TRANSACTION|PERFORMS_SIDE_EFFECT|WITHIN_TRANSACTION|BEFORE|AFTER|EXECUTES_SQL|EXECUTES_CYPHER|HAS_FINGERPRINT|READS_TABLE|WRITES_TABLE|READS_LABEL|WRITES_LABEL|JOINS_TABLE|USES_OPERATION|GENERATED_FROM_SPEC|BYPASSES_MANIFEST|TESTS_SYMBOL|TESTS_ENDPOINT|ASSERTS_CONTRACT|READS_ENV|SETS_ENV|USES_SECRET|GATES_CODE_PATH|CONTAINS]->()
WHERE r.path = path
DELETE r
WITH path
MATCH (artifact:RuntimeArtifact {path: path})
OPTIONAL MATCH (artifact)-[:CONTAINS]->(event:RuntimeEvent)
DETACH DELETE event, artifact
WITH 1 AS _
MATCH (n)
WHERE (
  n:Contract
  OR n:ContractField
  OR n:DependencyProvider
  OR n:AuthPolicy
  OR n:AuthScope
  OR n:EventFlow
  OR n:SqlQuery
  OR n:CypherQuery
  OR n:QueryFingerprint
  OR n:TransactionBoundary
  OR n:SideEffect
  OR n:ClientOperation
  OR n:TestSuite
  OR n:TestCase
  OR n:EnvVar
  OR n:FeatureFlag
  OR n:SecretRef
  OR (n:Queue AND coalesce(n.source_parser, '') = 'event_flow_pass')
  OR (n:DataStore AND coalesce(n.source_parser, '') = 'query_fingerprint_pass')
  OR (n:GraphNodeLabel AND coalesce(n.source_parser, '') = 'query_fingerprint_pass')
  OR (n:Endpoint AND coalesce(n.source_parser, '') = 'frontend_operation_pass')
)
  AND NOT (n)--()
DETACH DELETE n
"""


CYPHER_EXAMPLE_DECORATED_FUNCTIONS = f"""MATCH (n:Function|Method)
WHERE ANY(d IN n.decorators WHERE toLower(d) IN ['flow', 'task'])
RETURN n.name AS name, n.qualified_name AS qualified_name, labels(n) AS type
LIMIT {CYPHER_DEFAULT_LIMIT}"""
"""Example query to find functions/methods with specific decorators."""

CYPHER_EXAMPLE_CONTENT_BY_PATH = f"""MATCH (n)
WHERE n.path IS NOT NULL AND n.path STARTS WITH 'workflows'
RETURN n.name AS name, n.path AS path, labels(n) AS type
LIMIT {CYPHER_DEFAULT_LIMIT}"""
"""Example query to find all content within a specific path."""

CYPHER_EXAMPLE_KEYWORD_SEARCH = f"""MATCH (n)
WHERE toLower(n.name) CONTAINS 'database' OR (n.qualified_name IS NOT NULL AND toLower(n.qualified_name) CONTAINS 'database')
RETURN n.name AS name, n.qualified_name AS qualified_name, labels(n) AS type
LIMIT {CYPHER_DEFAULT_LIMIT}"""
"""Example query for a general keyword search across node names and qualified names."""

CYPHER_EXAMPLE_FIND_FILE = """MATCH (f:File) WHERE toLower(f.name) = 'readme.md' AND f.path = 'README.md'
RETURN f.path as path, f.name as name, labels(f) as type"""
"""Example query to find a specific file by name and path."""

CYPHER_EXAMPLE_README = f"""MATCH (f:File)
WHERE toLower(f.name) CONTAINS 'readme'
RETURN f.path AS path, f.name AS name, labels(f) AS type
LIMIT {CYPHER_DEFAULT_LIMIT}"""
"""Example query to find README files."""

CYPHER_EXAMPLE_PYTHON_FILES = f"""MATCH (f:File)
WHERE f.extension = '.py'
RETURN f.path AS path, f.name AS name, labels(f) AS type
LIMIT {CYPHER_DEFAULT_LIMIT}"""
"""Example query to find all Python files."""

CYPHER_EXAMPLE_TASKS = f"""MATCH (n:Function|Method)
WHERE 'task' IN n.decorators
RETURN n.qualified_name AS qualified_name, n.name AS name, labels(n) AS type
LIMIT {CYPHER_DEFAULT_LIMIT}"""
"""Example query to find functions/methods decorated as 'task'."""

CYPHER_EXAMPLE_FILES_IN_FOLDER = f"""MATCH (f:File)
WHERE f.path STARTS WITH 'services'
RETURN f.path AS path, f.name AS name, labels(f) AS type
LIMIT {CYPHER_DEFAULT_LIMIT}"""
"""Example query to list files within a specific folder."""

CYPHER_EXAMPLE_LIMIT_ONE = """MATCH (f:File) RETURN f.path as path, f.name as name, labels(f) as type LIMIT 1"""
"""Example query to get just one file, useful for testing."""

CYPHER_EXAMPLE_CLASS_METHODS = f"""MATCH (c:Class)-[:DEFINES_METHOD]->(m:Method)
WHERE c.qualified_name ENDS WITH '.UserService'
RETURN m.name AS name, m.qualified_name AS qualified_name, labels(m) AS type
LIMIT {CYPHER_DEFAULT_LIMIT}"""
"""Example query to find methods defined by a specific class."""

CYPHER_SEMANTIC_ENDPOINT_AUTH_COVERAGE = """
MATCH (e:Endpoint {project_name: $project_name})
OPTIONAL MATCH (e)-[:SECURED_BY]->(p:AuthPolicy)
OPTIONAL MATCH (p)-[:REQUIRES_SCOPE]->(s:AuthScope)
WITH e, collect(DISTINCT p.name) AS auth_policies, collect(DISTINCT s.name) AS auth_scopes
RETURN coalesce(e.route_path, e.route, e.name) AS endpoint,
       coalesce(e.http_method, e.method, 'ANY') AS method,
       auth_policies,
       auth_scopes,
       size(auth_policies) AS policy_count,
       size(auth_scopes) AS scope_count
ORDER BY policy_count ASC, endpoint
LIMIT 120
"""
"""Semantic preset query for endpoint auth coverage and scope visibility."""

CYPHER_SEMANTIC_ENDPOINT_DEPENDENCY_VISIBILITY = """
MATCH (e:Endpoint {project_name: $project_name})
OPTIONAL MATCH (e)-[:USES_DEPENDENCY]->(d:DependencyProvider)
WITH e, collect(DISTINCT d.name) AS dependencies
RETURN coalesce(e.route_path, e.route, e.name) AS endpoint,
       coalesce(e.http_method, e.method, 'ANY') AS method,
       dependencies,
       size(dependencies) AS dependency_count
ORDER BY dependency_count DESC, endpoint
LIMIT 120
"""
"""Semantic preset query for endpoint dependency-provider visibility."""

CYPHER_SEMANTIC_ENDPOINT_CONTRACT_GAPS = """
MATCH (e:Endpoint {project_name: $project_name})
OPTIONAL MATCH (e)-[:ACCEPTS_CONTRACT]->(req:Contract)
WITH e, collect(DISTINCT req.name) AS request_contracts
OPTIONAL MATCH (e)-[:RETURNS_CONTRACT]->(resp:Contract)
WITH e, request_contracts, collect(DISTINCT resp.name) AS response_contracts
OPTIONAL MATCH (caller)-[:REQUESTS_ENDPOINT]->(e)
WITH e,
     request_contracts,
     response_contracts,
     count(DISTINCT caller) AS requester_count
WHERE request_contracts = [] OR response_contracts = []
RETURN coalesce(e.route_path, e.route, e.name) AS endpoint,
       coalesce(e.http_method, e.method, 'ANY') AS method,
       request_contracts,
       response_contracts,
       requester_count
ORDER BY requester_count DESC, endpoint
LIMIT 120
"""
"""Semantic preset query for contract-gap and drift-candidate endpoints."""

CYPHER_SEMANTIC_UNPROTECTED_ENDPOINTS = """
MATCH (e:Endpoint {project_name: $project_name})
OPTIONAL MATCH (e)-[:SECURED_BY]->(p:AuthPolicy)
WITH e, collect(DISTINCT p.name) AS auth_policies
WHERE auth_policies = []
RETURN coalesce(e.route_path, e.route, e.name) AS endpoint,
       coalesce(e.http_method, e.method, 'ANY') AS method,
       coalesce(e.path, '') AS path
ORDER BY endpoint
LIMIT 120
"""
"""Semantic preset query for endpoints without explicit auth policy coverage."""

CYPHER_EVENT_OUTBOX_WITHOUT_TRANSACTION = """
MATCH (producer {project_name: $project_name})-[:WRITES_OUTBOX]->(flow:EventFlow {project_name: $project_name})
OPTIONAL MATCH (producer)-[:PERFORMS_SIDE_EFFECT]->(effect:SideEffect {project_name: $project_name, effect_kind: 'outbox_write'})-[:WITHIN_TRANSACTION]->(tx:TransactionBoundary {project_name: $project_name})
WITH producer,
     flow,
     collect(DISTINCT effect.qualified_name) AS outbox_effects,
     collect(DISTINCT tx.qualified_name) AS transaction_boundaries
WHERE size(transaction_boundaries) = 0
RETURN producer.qualified_name AS producer,
       flow.name AS event_flow,
       coalesce(flow.channel_name, '') AS queue_name,
       size(outbox_effects) AS outbox_effect_count,
       size(transaction_boundaries) AS transaction_count
ORDER BY producer
LIMIT 120
"""
"""Semantic preset query for outbox writes without transaction evidence."""

CYPHER_EVENT_CONSUMER_WITHOUT_DLQ = """
MATCH (handler {project_name: $project_name})-[:CONSUMES_EVENT]->(flow:EventFlow {project_name: $project_name})
OPTIONAL MATCH (handler)-[:WRITES_DLQ]->(dlq:Queue {project_name: $project_name})
WITH handler,
     flow,
     collect(DISTINCT dlq.name) AS dlq_queues
WHERE coalesce(flow.dlq_name, '') = '' AND dlq_queues = []
RETURN handler.qualified_name AS handler,
       flow.name AS event_flow,
       coalesce(flow.channel_name, '') AS queue_name,
       dlq_queues
ORDER BY handler
LIMIT 120
"""
"""Semantic preset query for consumers without any DLQ path."""

CYPHER_EVENT_REPLAY_PATHS = """
MATCH (replayer {project_name: $project_name})-[:REPLAYS_EVENT]->(flow:EventFlow {project_name: $project_name})
OPTIONAL MATCH (flow)-[:USES_HANDLER]->(handler {project_name: $project_name})
OPTIONAL MATCH (flow)-[queue_rel:USES_QUEUE]->(queue:Queue {project_name: $project_name})
RETURN replayer.qualified_name AS replayer,
       flow.name AS event_flow,
       coalesce(flow.event_name, '') AS event_name,
       collect(DISTINCT handler.qualified_name) AS handlers,
       collect(DISTINCT {
         queue_name: queue.name,
         queue_role: coalesce(queue_rel.queue_role, '')
       }) AS queues
ORDER BY replayer
LIMIT 120
"""
"""Semantic preset query for replay entrypoints and their downstream handlers/queues."""

CYPHER_TRANSACTION_EXTERNAL_CALL_BEFORE_COMMIT = """
MATCH (actor {project_name: $project_name})-[:COMMITS_TRANSACTION]->(tx:TransactionBoundary {project_name: $project_name})
MATCH (actor)-[:PERFORMS_SIDE_EFFECT]->(effect:SideEffect {project_name: $project_name, effect_kind: 'external_http'})-[:WITHIN_TRANSACTION]->(tx)
RETURN actor.qualified_name AS actor,
       tx.qualified_name AS transaction_boundary,
       effect.qualified_name AS side_effect,
       coalesce(effect.operation_name, effect.name, '') AS operation_name
ORDER BY actor, side_effect
LIMIT 120
"""
"""Semantic preset query for external HTTP side effects that occur inside committing transactions."""

CYPHER_EVENT_DUPLICATE_PUBLISHERS = """
MATCH (publisher {project_name: $project_name})-[:PUBLISHES_EVENT]->(flow:EventFlow {project_name: $project_name})
WITH flow,
     collect(DISTINCT publisher.qualified_name) AS publishers
WHERE size(publishers) > 1
RETURN flow.name AS event_flow,
       coalesce(flow.event_name, '') AS event_name,
       coalesce(flow.channel_name, '') AS queue_name,
       publishers,
       size(publishers) AS publisher_count
ORDER BY publisher_count DESC, event_flow
LIMIT 120
"""
"""Semantic preset query for event flows with multiple publishers."""


CYPHER_FRONTEND_CLIENT_OPERATIONS = """
MATCH (op:ClientOperation {project_name: $project_name})-[:REQUESTS_ENDPOINT]->(endpoint:Endpoint {project_name: $project_name})
RETURN op.name AS operation_name,
       coalesce(op.operation_id, '') AS operation_id,
       coalesce(op.governance_kind, '') AS governance_kind,
       coalesce(op.client_kind, '') AS client_kind,
       endpoint.http_method AS method,
       endpoint.route_path AS path,
       endpoint.qualified_name AS endpoint_qn
ORDER BY governance_kind DESC, method, path, operation_name
LIMIT 120
"""
"""Semantic preset query for governed and bypass client operations."""


CYPHER_FRONTEND_BYPASSES_MANIFEST = """
MATCH (op:ClientOperation {project_name: $project_name})-[:BYPASSES_MANIFEST]->(endpoint:Endpoint {project_name: $project_name})
RETURN op.name AS operation_name,
       coalesce(op.operation_id, '') AS operation_id,
       coalesce(op.client_kind, '') AS client_kind,
       endpoint.http_method AS method,
       endpoint.route_path AS path,
       endpoint.qualified_name AS endpoint_qn
ORDER BY method, path, operation_name
LIMIT 120
"""
"""Semantic preset query for raw client operations bypassing the manifest/spec."""


CYPHER_TEST_UNTESTED_PUBLIC_ENDPOINTS = """
MATCH (endpoint:Endpoint {project_name: $project_name})
OPTIONAL MATCH (endpoint)<-[:TESTS_ENDPOINT]-(direct_case:TestCase {project_name: $project_name})
OPTIONAL MATCH (endpoint)-[:ACCEPTS_CONTRACT|RETURNS_CONTRACT]->(contract:Contract {project_name: $project_name})<-[:ASSERTS_CONTRACT]-(contract_case:TestCase {project_name: $project_name})
WITH endpoint,
     [value IN collect(DISTINCT direct_case.qualified_name) + collect(DISTINCT contract_case.qualified_name) WHERE value IS NOT NULL] AS testcase_qns
WHERE size(testcase_qns) = 0
RETURN endpoint.route_path AS endpoint,
       endpoint.http_method AS method,
       endpoint.qualified_name AS endpoint_qn
ORDER BY method, endpoint
LIMIT 120
"""
"""Semantic preset query for public endpoints that have no direct or contract-driven testcase coverage."""


CYPHER_TEST_CONTRACT_COVERAGE = """
MATCH (contract:Contract {project_name: $project_name})
OPTIONAL MATCH (contract)<-[:ASSERTS_CONTRACT]-(testcase:TestCase {project_name: $project_name})
RETURN contract.name AS contract_name,
       contract.qualified_name AS contract_qn,
       count(DISTINCT testcase) AS testcase_count,
       collect(DISTINCT testcase.path)[0..10] AS test_files
ORDER BY testcase_count ASC, contract_name
LIMIT 120
"""
"""Semantic preset query for testcase-to-contract coverage."""


CYPHER_CONFIG_UNDEFINED_ENV_READERS = """
MATCH (reader {project_name: $project_name})-[:READS_ENV]->(env:EnvVar {project_name: $project_name})
OPTIONAL MATCH (resource:InfraResource {project_name: $project_name})-[:SETS_ENV]->(env)
WITH reader,
     env,
     collect(DISTINCT resource.qualified_name) AS resource_qns
WHERE coalesce(env.has_definition, false) = false
RETURN reader.qualified_name AS reader,
       labels(reader)[0] AS reader_type,
       env.name AS env_name,
       env.qualified_name AS env_qn,
       resource_qns,
       size(resource_qns) AS resource_count
ORDER BY env_name, reader
LIMIT 120
"""
"""Semantic preset query for code readers that consume env vars with no known definition."""


CYPHER_CONFIG_ORPHAN_SECRET_REFS = """
MATCH (secret:SecretRef {project_name: $project_name})
OPTIONAL MATCH (resource:InfraResource {project_name: $project_name})-[:USES_SECRET]->(secret)
OPTIONAL MATCH (reader {project_name: $project_name})-[:USES_SECRET]->(secret)
WITH secret,
     collect(DISTINCT resource.qualified_name) AS resource_qns,
     collect(DISTINCT reader.qualified_name) AS reader_qns
WHERE resource_qns = [] OR reader_qns = []
RETURN secret.name AS secret_name,
       secret.qualified_name AS secret_qn,
       resource_qns,
       reader_qns,
       CASE
           WHEN resource_qns = [] AND reader_qns = [] THEN "unbound"
           WHEN resource_qns = [] THEN "reader_only"
           ELSE "resource_only"
       END AS binding_status
ORDER BY secret_name
LIMIT 120
"""
"""Semantic preset query for secret refs that are only defined or only consumed."""


CYPHER_CONFIG_UNBOUND_SECRET_REFS = CYPHER_CONFIG_ORPHAN_SECRET_REFS
"""Semantic preset alias for unbound or one-sided secret refs."""


CYPHER_CONFIG_UNUSED_FEATURE_FLAGS = """
MATCH (flag:FeatureFlag {project_name: $project_name})
OPTIONAL MATCH (flag)-[:GATES_CODE_PATH]->(target {project_name: $project_name})
OPTIONAL MATCH (resource:InfraResource {project_name: $project_name})-[:SETS_ENV]->(env:EnvVar {project_name: $project_name})
WHERE env.name = flag.name
WITH flag,
     collect(DISTINCT target.qualified_name) AS gated_targets,
     collect(DISTINCT resource.qualified_name) AS resource_qns
WHERE gated_targets = []
RETURN flag.name AS flag_name,
       flag.qualified_name AS flag_qn,
       resource_qns,
       coalesce(flag.default_enabled, false) AS default_enabled
ORDER BY flag_name
LIMIT 120
"""
"""Semantic preset query for feature flags that have definitions but no gated code path."""


CYPHER_CONFIG_ORPHAN_FEATURE_FLAGS = """
MATCH (flag:FeatureFlag {project_name: $project_name})
OPTIONAL MATCH (flag)-[:GATES_CODE_PATH]->(target {project_name: $project_name})
OPTIONAL MATCH (resource:InfraResource {project_name: $project_name})-[:SETS_ENV]->(env:EnvVar {project_name: $project_name})
WHERE env.name = flag.name
WITH flag,
     collect(DISTINCT target.qualified_name) AS gated_targets,
     collect(DISTINCT resource.qualified_name) AS resource_qns
WHERE resource_qns = [] OR gated_targets = []
RETURN flag.name AS flag_name,
       flag.qualified_name AS flag_qn,
       resource_qns,
       gated_targets,
       CASE
           WHEN resource_qns = [] AND gated_targets = [] THEN "orphan"
           WHEN resource_qns = [] THEN "reader_only"
           ELSE "resource_only"
       END AS drift_kind,
       coalesce(flag.default_enabled, false) AS default_enabled
ORDER BY flag_name
LIMIT 120
"""
"""Semantic preset query for feature flags with missing infra binding or missing gated path evidence."""


CYPHER_CONFIG_RESOURCE_WITHOUT_READERS = """
MATCH (resource:InfraResource {project_name: $project_name})-[:SETS_ENV]->(env:EnvVar {project_name: $project_name})
OPTIONAL MATCH (reader {project_name: $project_name})-[:READS_ENV]->(env)
WITH resource,
     env,
     collect(DISTINCT reader.qualified_name) AS reader_qns
WHERE reader_qns = []
RETURN resource.qualified_name AS resource_qn,
       env.name AS env_name,
       env.qualified_name AS env_qn,
       reader_qns
ORDER BY resource_qn, env_name
LIMIT 120
"""
"""Semantic preset query for infra resources that project env vars with no known readers."""


CYPHER_CONFIG_READER_WITHOUT_RESOURCE = """
MATCH (reader {project_name: $project_name})-[:READS_ENV]->(env:EnvVar {project_name: $project_name})
OPTIONAL MATCH (resource:InfraResource {project_name: $project_name})-[:SETS_ENV]->(env)
WITH reader,
     env,
     collect(DISTINCT resource.qualified_name) AS resource_qns
WHERE resource_qns = []
RETURN reader.qualified_name AS reader,
       labels(reader)[0] AS reader_type,
       env.name AS env_name,
       env.qualified_name AS env_qn,
       coalesce(env.has_definition, false) AS has_definition,
       resource_qns
ORDER BY env_name, reader
LIMIT 120
"""
"""Semantic preset query for env readers that lack any infra-resource projection."""


CYPHER_VALIDATION_FASTAPI_AUTH_CONTRACT_MINIMUM = """
MATCH (endpoint:Endpoint {project_name: $project_name})-[:USES_DEPENDENCY]->(:DependencyProvider {project_name: $project_name})
MATCH (endpoint)-[:SECURED_BY]->(policy:AuthPolicy {project_name: $project_name})-[:REQUIRES_SCOPE]->(:AuthScope {project_name: $project_name})
MATCH (endpoint)-[:ACCEPTS_CONTRACT]->(:Contract {project_name: $project_name})
MATCH (endpoint)-[:RETURNS_CONTRACT]->(:Contract {project_name: $project_name})
RETURN count(DISTINCT endpoint) AS matched_rows
LIMIT 1
"""
"""Canonical validation query for FastAPI auth/dependency/contract semantics."""


CYPHER_VALIDATION_EVENT_FLOW_MINIMUM = """
MATCH (:Function {project_name: $project_name})-[:WRITES_OUTBOX]->(flow:EventFlow {project_name: $project_name})
MATCH (:Function {project_name: $project_name})-[:PUBLISHES_EVENT]->(flow)
MATCH (flow)-[:USES_HANDLER]->(:Method {project_name: $project_name})
MATCH (:Function {project_name: $project_name})-[:REPLAYS_EVENT]->(flow)
MATCH (:Method {project_name: $project_name})-[:WRITES_DLQ]->(:Queue {project_name: $project_name})
RETURN count(DISTINCT flow) AS matched_rows
LIMIT 1
"""
"""Canonical validation query for event/outbox/consumer/replay semantics."""


CYPHER_VALIDATION_TRANSACTION_MINIMUM = """
MATCH (:Function {project_name: $project_name})-[:BEGINS_TRANSACTION]->(boundary:TransactionBoundary {project_name: $project_name})
MATCH (:Function {project_name: $project_name})-[:COMMITS_TRANSACTION]->(boundary)
MATCH (:Function {project_name: $project_name})-[:PERFORMS_SIDE_EFFECT]->(effect:SideEffect {project_name: $project_name})-[:WITHIN_TRANSACTION]->(boundary)
MATCH (effect)-[:BEFORE]->(:SideEffect {project_name: $project_name})
RETURN count(DISTINCT boundary) AS matched_rows
LIMIT 1
"""
"""Canonical validation query for transaction boundary and side-effect ordering semantics."""


CYPHER_VALIDATION_QUERY_FINGERPRINT_MINIMUM = """
MATCH (:Function {project_name: $project_name})-[:EXECUTES_SQL]->(sql:SqlQuery {project_name: $project_name})-[:HAS_FINGERPRINT]->(:QueryFingerprint {project_name: $project_name})
MATCH (sql)-[:READS_TABLE]->(:DataStore {project_name: $project_name})
WITH collect(DISTINCT sql) AS sql_queries
MATCH (:Function {project_name: $project_name})-[:EXECUTES_CYPHER]->(cypher:CypherQuery {project_name: $project_name})-[:HAS_FINGERPRINT]->(:QueryFingerprint {project_name: $project_name})
MATCH (cypher)-[:WRITES_LABEL]->(:GraphNodeLabel {project_name: $project_name})
RETURN size(sql_queries) + count(DISTINCT cypher) AS matched_rows
LIMIT 1
"""
"""Canonical validation query for SQL/Cypher query fingerprint semantics."""


CYPHER_VALIDATION_FRONTEND_OPERATION_MINIMUM = """
MATCH (:Component {project_name: $project_name})-[:USES_OPERATION]->(governed:ClientOperation {project_name: $project_name})-[:REQUESTS_ENDPOINT]->(:Endpoint {project_name: $project_name})
WITH collect(DISTINCT governed) AS governed_ops
MATCH (:Function {project_name: $project_name})-[:USES_OPERATION]->(raw:ClientOperation {project_name: $project_name})-[:BYPASSES_MANIFEST]->(:Endpoint {project_name: $project_name})
RETURN size(governed_ops) + count(DISTINCT raw) AS matched_rows
LIMIT 1
"""
"""Canonical validation query for frontend operation governance semantics."""


CYPHER_VALIDATION_TEST_SEMANTICS_MINIMUM = """
MATCH (:TestSuite {project_name: $project_name})-[:CONTAINS]->(testcase:TestCase {project_name: $project_name})
MATCH (testcase)-[:TESTS_SYMBOL]->(:Function {project_name: $project_name})
MATCH (testcase)-[:TESTS_ENDPOINT]->(:Endpoint {project_name: $project_name})
MATCH (testcase)-[:ASSERTS_CONTRACT]->(:Contract {project_name: $project_name})
RETURN count(DISTINCT testcase) AS matched_rows
LIMIT 1
"""
"""Canonical validation query for testcase/symbol/endpoint/contract semantics."""


CYPHER_VALIDATION_CONFIG_CONTROL_PLANE_MINIMUM = """
MATCH (:Function {project_name: $project_name})-[:READS_ENV]->(env:EnvVar {project_name: $project_name})
MATCH (:InfraResource {project_name: $project_name})-[:SETS_ENV]->(env)
MATCH (:Function {project_name: $project_name})-[:USES_SECRET]->(:SecretRef {project_name: $project_name})
MATCH (:FeatureFlag {project_name: $project_name})-[:GATES_CODE_PATH]->(:Function {project_name: $project_name})
RETURN count(DISTINCT env) AS matched_rows
LIMIT 1
"""
"""Canonical validation query for env/flag/secret control-plane semantics."""


def build_semantic_auth_contract_query_pack() -> list[dict[str, str]]:
    """Returns canned semantic auth/contract Cypher presets."""

    return [
        {
            "name": "endpoint_auth_coverage",
            "summary": "List endpoints with auth policy and scope coverage.",
            "cypher": CYPHER_SEMANTIC_ENDPOINT_AUTH_COVERAGE,
        },
        {
            "name": "endpoint_dependency_visibility",
            "summary": "List endpoint dependency providers and fan-in count.",
            "cypher": CYPHER_SEMANTIC_ENDPOINT_DEPENDENCY_VISIBILITY,
        },
        {
            "name": "endpoint_contract_gaps",
            "summary": "Find endpoints that are missing request or response contracts.",
            "cypher": CYPHER_SEMANTIC_ENDPOINT_CONTRACT_GAPS,
        },
        {
            "name": "unprotected_endpoints",
            "summary": "Find endpoints without any explicit auth policy edge.",
            "cypher": CYPHER_SEMANTIC_UNPROTECTED_ENDPOINTS,
        },
    ]


def build_event_reliability_query_pack() -> list[dict[str, str]]:
    """Returns canned event-reliability and transaction-safety Cypher presets."""

    return [
        {
            "name": "outbox_without_transaction",
            "summary": "Find outbox writers without transaction evidence.",
            "cypher": CYPHER_EVENT_OUTBOX_WITHOUT_TRANSACTION,
        },
        {
            "name": "consumer_without_dlq",
            "summary": "Find consumers that have no DLQ path.",
            "cypher": CYPHER_EVENT_CONSUMER_WITHOUT_DLQ,
        },
        {
            "name": "replay_paths",
            "summary": "List replay entrypoints with handler and queue paths.",
            "cypher": CYPHER_EVENT_REPLAY_PATHS,
        },
        {
            "name": "external_call_before_commit",
            "summary": "Find external HTTP calls that occur inside committing transactions.",
            "cypher": CYPHER_TRANSACTION_EXTERNAL_CALL_BEFORE_COMMIT,
        },
        {
            "name": "duplicate_publishers",
            "summary": "Find event flows that are published by multiple producers.",
            "cypher": CYPHER_EVENT_DUPLICATE_PUBLISHERS,
        },
    ]


def build_frontend_operation_query_pack() -> list[dict[str, str]]:
    """Returns canned frontend operation governance Cypher presets."""

    return [
        {
            "name": "client_operations",
            "summary": "List governed and bypass frontend client operations.",
            "cypher": CYPHER_FRONTEND_CLIENT_OPERATIONS,
        },
        {
            "name": "bypasses_manifest",
            "summary": "Find frontend calls that bypass the generated manifest/spec path.",
            "cypher": CYPHER_FRONTEND_BYPASSES_MANIFEST,
        },
    ]


def build_test_semantics_query_pack() -> list[dict[str, str]]:
    """Returns canned semantic test-coverage Cypher presets."""

    return [
        {
            "name": "untested_public_endpoints",
            "summary": "Find endpoints without testcase coverage through endpoint or contract edges.",
            "cypher": CYPHER_TEST_UNTESTED_PUBLIC_ENDPOINTS,
        },
        {
            "name": "contract_test_coverage",
            "summary": "Inspect testcase coverage for each contract node.",
            "cypher": CYPHER_TEST_CONTRACT_COVERAGE,
        },
    ]


def build_config_runtime_query_pack() -> list[dict[str, str]]:
    """Returns canned config/runtime semantic Cypher presets."""

    return [
        {
            "name": "undefined_env_readers",
            "summary": "Find code readers that consume env vars with no known definition or resource projection.",
            "cypher": CYPHER_CONFIG_UNDEFINED_ENV_READERS,
        },
        {
            "name": "orphan_secret_refs",
            "summary": "Find secret refs that are only defined by infra or only consumed by code.",
            "cypher": CYPHER_CONFIG_ORPHAN_SECRET_REFS,
        },
        {
            "name": "unbound_secret_refs",
            "summary": "Find secret refs that are missing either infra bindings or code readers.",
            "cypher": CYPHER_CONFIG_UNBOUND_SECRET_REFS,
        },
        {
            "name": "unused_feature_flags",
            "summary": "Find feature flags that have no gated code path evidence.",
            "cypher": CYPHER_CONFIG_UNUSED_FEATURE_FLAGS,
        },
        {
            "name": "orphan_feature_flags",
            "summary": "Find feature flags that have missing infra bindings or missing gated code-path evidence.",
            "cypher": CYPHER_CONFIG_ORPHAN_FEATURE_FLAGS,
        },
        {
            "name": "resource_without_readers",
            "summary": "Find infra resources that project env vars with no known code readers.",
            "cypher": CYPHER_CONFIG_RESOURCE_WITHOUT_READERS,
        },
        {
            "name": "reader_without_resource",
            "summary": "Find env readers that have no matching infra-resource projection.",
            "cypher": CYPHER_CONFIG_READER_WITHOUT_RESOURCE,
        },
    ]


def build_semantic_validation_query_pack() -> list[dict[str, object]]:
    """Returns canonical semantic validation queries with fixture mappings."""

    return [
        {
            "name": "fastapi_auth_contract_minimum",
            "summary": "Validate minimum FastAPI dependency, auth, scope, and contract coverage.",
            "fixture_name": "fastapi_semantic_fixture",
            "minimum_rows": 1,
            "cypher": CYPHER_VALIDATION_FASTAPI_AUTH_CONTRACT_MINIMUM,
        },
        {
            "name": "event_flow_minimum",
            "summary": "Validate minimum outbox, publish, handler, replay, and DLQ event coverage.",
            "fixture_name": "event_flow_semantic_fixture",
            "minimum_rows": 1,
            "cypher": CYPHER_VALIDATION_EVENT_FLOW_MINIMUM,
        },
        {
            "name": "transaction_flow_minimum",
            "summary": "Validate minimum transaction-boundary and side-effect ordering coverage.",
            "fixture_name": "transaction_flow_semantic_fixture",
            "minimum_rows": 1,
            "cypher": CYPHER_VALIDATION_TRANSACTION_MINIMUM,
        },
        {
            "name": "query_fingerprint_minimum",
            "summary": "Validate minimum SQL/Cypher query, fingerprint, and target coverage.",
            "fixture_name": "query_fingerprint_semantic_fixture",
            "minimum_rows": 1,
            "cypher": CYPHER_VALIDATION_QUERY_FINGERPRINT_MINIMUM,
        },
        {
            "name": "frontend_operation_minimum",
            "summary": "Validate minimum governed-operation and raw-bypass frontend coverage.",
            "fixture_name": "frontend_operation_semantic_fixture",
            "minimum_rows": 1,
            "cypher": CYPHER_VALIDATION_FRONTEND_OPERATION_MINIMUM,
        },
        {
            "name": "test_semantics_minimum",
            "summary": "Validate minimum testcase-to-symbol/endpoint/contract coverage.",
            "fixture_name": "test_semantics_fixture",
            "minimum_rows": 1,
            "cypher": CYPHER_VALIDATION_TEST_SEMANTICS_MINIMUM,
        },
        {
            "name": "config_control_plane_minimum",
            "summary": "Validate minimum env-reader, infra-setter, secret, and feature-flag coverage.",
            "fixture_name": "env_flag_secret_semantic_fixture",
            "minimum_rows": 1,
            "cypher": CYPHER_VALIDATION_CONFIG_CONTROL_PLANE_MINIMUM,
        },
    ]


CYPHER_EXPORT_NODES = """
MATCH (n)
RETURN id(n) as node_id, labels(n) as labels, properties(n) as properties
"""
"""Exports all nodes with their ID, labels, and properties."""

CYPHER_EXPORT_RELATIONSHIPS = """
MATCH (a)-[r]->(b)
RETURN id(a) as from_id, id(b) as to_id, type(r) as type, properties(r) as properties
"""
"""Exports all relationships with their source/target IDs, type, and properties."""

CYPHER_EXPORT_PROJECT_NODES = """
MATCH (p:Project {name: $project_name})
OPTIONAL MATCH (p)-[:CONTAINS_PACKAGE|CONTAINS_FOLDER|CONTAINS_FILE|CONTAINS_MODULE*0..]->(container)
WITH collect(DISTINCT p) + collect(DISTINCT container) AS seed
UNWIND seed AS s
MATCH (s)-[*0..2]-(n)
RETURN DISTINCT id(n) as node_id, labels(n) as labels, properties(n) as properties
"""

CYPHER_EXPORT_PROJECT_RELATIONSHIPS = """
MATCH (p:Project {name: $project_name})
OPTIONAL MATCH (p)-[:CONTAINS_PACKAGE|CONTAINS_FOLDER|CONTAINS_FILE|CONTAINS_MODULE*0..]->(container)
WITH collect(DISTINCT p) + collect(DISTINCT container) AS seed
UNWIND seed AS s
MATCH (s)-[*0..2]-(n)
WITH collect(DISTINCT n) AS nodes
UNWIND nodes AS n
MATCH (n)-[r]->(m)
WHERE m IN nodes
RETURN id(n) as from_id, id(m) as to_id, type(r) as type, properties(r) as properties
"""

CYPHER_EXPORT_PROJECT_NODES_PAGED = """
MATCH (p:Project {name: $project_name})
OPTIONAL MATCH (p)-[:CONTAINS_PACKAGE|CONTAINS_FOLDER|CONTAINS_FILE|CONTAINS_MODULE*0..]->(container)
WITH collect(DISTINCT p) + collect(DISTINCT container) AS seed
UNWIND seed AS s
MATCH (s)-[*0..2]-(n)
RETURN DISTINCT id(n) as node_id, labels(n) as labels, properties(n) as properties
SKIP $offset LIMIT $limit
"""

CYPHER_EXPORT_PROJECT_RELATIONSHIPS_PAGED = """
MATCH (p:Project {name: $project_name})
OPTIONAL MATCH (p)-[:CONTAINS_PACKAGE|CONTAINS_FOLDER|CONTAINS_FILE|CONTAINS_MODULE*0..]->(container)
WITH collect(DISTINCT p) + collect(DISTINCT container) AS seed
UNWIND seed AS s
MATCH (s)-[*0..2]-(n)
WITH collect(DISTINCT n) AS nodes
UNWIND nodes AS n
MATCH (n)-[r]->(m)
WHERE m IN nodes
RETURN id(n) as from_id, id(m) as to_id, type(r) as type, properties(r) as properties
SKIP $offset LIMIT $limit
"""

CYPHER_RETURN_COUNT = "RETURN count(r) as created"
"""A query fragment to return the count of created relationships."""
CYPHER_SET_PROPS_RETURN_COUNT = "SET r += row.props\nRETURN count(r) as created"
"""A query fragment to set properties on a relationship and return the count."""

CYPHER_GET_FUNCTION_SOURCE_LOCATION = """
MATCH (m:Module)-[:DEFINES]->(n)
WHERE id(n) = $node_id
RETURN n.qualified_name AS qualified_name, n.start_line AS start_line,
       n.end_line AS end_line, m.path AS path
"""
"""Retrieves the source code location (file path, start/end lines) for a given node ID."""

CYPHER_FIND_BY_QUALIFIED_NAME = """
MATCH (n) WHERE n.qualified_name = $qn
OPTIONAL MATCH (m:Module)-[*]-(n)
RETURN n.name AS name,
       n.start_line AS start,
       n.end_line AS end,
       m.path AS path,
       n.docstring AS docstring,
       coalesce(n.project_name, m.project_name) AS project_name
LIMIT 1
"""
"""Finds a node by its fully qualified name and returns its details."""

CYPHER_ANALYSIS_USAGE = """
MATCH (m:Module {project_name: $project_name})-[:DEFINES|DEFINES_METHOD*0..1]->(node)
WITH DISTINCT node
OPTIONAL MATCH ()-[r:CALLS|USES_COMPONENT|REQUESTS_ENDPOINT|RESOLVES_IMPORT|USES_ASSET|HANDLES_ERROR|MUTATES_STATE]->(node)
RETURN node.qualified_name AS qualified_name,
             labels(node)[0] AS label,
             count(r) AS usage_count
"""

CYPHER_ANALYSIS_USAGE_FILTERED = """
MATCH (m:Module {project_name: $project_name})-[:DEFINES|DEFINES_METHOD*0..1]->(node)
WHERE $module_paths IS NULL OR m.path IN $module_paths
WITH DISTINCT node
OPTIONAL MATCH ()-[r:CALLS|USES_COMPONENT|REQUESTS_ENDPOINT|RESOLVES_IMPORT|USES_ASSET|HANDLES_ERROR|MUTATES_STATE]->(node)
RETURN node.qualified_name AS qualified_name,
             labels(node)[0] AS label,
             count(r) AS usage_count
"""

CYPHER_ANALYSIS_DEAD_CODE = """
MATCH (m:Module {project_name: $project_name})-[:DEFINES|DEFINES_METHOD*0..1]->(f)
WHERE (f:Function OR f:Method)
    AND (f.is_exported IS NULL OR f.is_exported = false)
    AND coalesce(f.is_entry_point, false) = false
WITH f, min(m.path) AS path
OPTIONAL MATCH (decorator_src)-[:DECORATES|ANNOTATES]->(f)
WITH f, path, count(DISTINCT decorator_src) AS decorator_links
WHERE decorator_links = 0
OPTIONAL MATCH (registration_src)-[:HAS_ENDPOINT|ROUTES_TO_CONTROLLER|ROUTES_TO_ACTION|REQUESTS_ENDPOINT|REGISTERS_SERVICE|HOOKS|REGISTERS_BLOCK|USES_HANDLER|USES_SERVICE|PROVIDES_SERVICE]->(f)
WITH f, path, decorator_links, count(DISTINCT registration_src) AS registration_links
WHERE registration_links = 0
OPTIONAL MATCH (caller)-[:CALLS]->(f)
WITH f, path, decorator_links, registration_links, count(DISTINCT caller) AS call_in_degree
WHERE call_in_degree = 0
OPTIONAL MATCH (f)-[:CALLS]->(callee)
WITH f, path, decorator_links, registration_links, call_in_degree, count(DISTINCT callee) AS out_call_count
OPTIONAL MATCH (import_src)-[:USES_HANDLER|USES_SERVICE|REQUESTS_ENDPOINT|ROUTES_TO_ACTION]->(f)
WITH f, path, decorator_links, registration_links, call_in_degree, out_call_count, count(DISTINCT import_src) AS imported_by_cli_links
OPTIONAL MATCH (config_src)-[:IMPORTS|RESOLVES_IMPORT|USES_COMPONENT]->(f)
WITH f, path, decorator_links, registration_links, call_in_degree, out_call_count,
     imported_by_cli_links, count(DISTINCT config_src) AS config_reference_links
WITH f, path, call_in_degree, out_call_count, decorator_links, registration_links,
     imported_by_cli_links, config_reference_links,
     CASE WHEN f.name IN $entry_names THEN true ELSE false END AS is_entrypoint_name,
     CASE WHEN ANY(d IN coalesce(f.decorators, []) WHERE d IN $decorators) THEN true ELSE false END AS has_entry_decorator
RETURN DISTINCT f.qualified_name AS qualified_name,
                f.name AS name,
                path AS path,
                f.start_line AS start_line,
                labels(f)[0] AS label,
                call_in_degree,
                out_call_count,
                is_entrypoint_name,
                has_entry_decorator,
                decorator_links,
                registration_links,
                imported_by_cli_links,
                config_reference_links,
                coalesce(f.decorators, []) AS decorators,
                coalesce(f.is_exported, false) AS is_exported
ORDER BY path, start_line
LIMIT $dead_code_limit
"""

CYPHER_ANALYSIS_DEAD_CODE_FILTERED = """
MATCH (m:Module {project_name: $project_name})-[:DEFINES|DEFINES_METHOD*0..1]->(f)
WHERE (f:Function OR f:Method)
    AND ($module_paths IS NULL OR m.path IN $module_paths)
    AND (f.is_exported IS NULL OR f.is_exported = false)
    AND coalesce(f.is_entry_point, false) = false
WITH f, min(m.path) AS path
OPTIONAL MATCH (decorator_src)-[:DECORATES|ANNOTATES]->(f)
WITH f, path, count(DISTINCT decorator_src) AS decorator_links
WHERE decorator_links = 0
OPTIONAL MATCH (registration_src)-[:HAS_ENDPOINT|ROUTES_TO_CONTROLLER|ROUTES_TO_ACTION|REQUESTS_ENDPOINT|REGISTERS_SERVICE|HOOKS|REGISTERS_BLOCK|USES_HANDLER|USES_SERVICE|PROVIDES_SERVICE]->(f)
WITH f, path, decorator_links, count(DISTINCT registration_src) AS registration_links
WHERE registration_links = 0
OPTIONAL MATCH (caller)-[:CALLS]->(f)
WITH f, path, decorator_links, registration_links, count(DISTINCT caller) AS call_in_degree
WHERE call_in_degree = 0
OPTIONAL MATCH (f)-[:CALLS]->(callee)
WITH f, path, decorator_links, registration_links, call_in_degree, count(DISTINCT callee) AS out_call_count
OPTIONAL MATCH (import_src)-[:USES_HANDLER|USES_SERVICE|REQUESTS_ENDPOINT|ROUTES_TO_ACTION]->(f)
WITH f, path, decorator_links, registration_links, call_in_degree, out_call_count, count(DISTINCT import_src) AS imported_by_cli_links
OPTIONAL MATCH (config_src)-[:IMPORTS|RESOLVES_IMPORT|USES_COMPONENT]->(f)
WITH f, path, decorator_links, registration_links, call_in_degree, out_call_count,
     imported_by_cli_links, count(DISTINCT config_src) AS config_reference_links
WITH f, path, call_in_degree, out_call_count, decorator_links, registration_links,
     imported_by_cli_links, config_reference_links,
     CASE WHEN f.name IN $entry_names THEN true ELSE false END AS is_entrypoint_name,
     CASE WHEN ANY(d IN coalesce(f.decorators, []) WHERE d IN $decorators) THEN true ELSE false END AS has_entry_decorator
RETURN DISTINCT f.qualified_name AS qualified_name,
                f.name AS name,
                path AS path,
                f.start_line AS start_line,
                labels(f)[0] AS label,
                call_in_degree,
                out_call_count,
                is_entrypoint_name,
                has_entry_decorator,
                decorator_links,
                registration_links,
                imported_by_cli_links,
                config_reference_links,
                coalesce(f.decorators, []) AS decorators,
                coalesce(f.is_exported, false) AS is_exported
ORDER BY path, start_line
LIMIT $dead_code_limit
"""

CYPHER_ANALYSIS_TOTAL_FUNCTIONS = """
MATCH (m:Module {project_name: $project_name})-[:DEFINES|DEFINES_METHOD*0..1]->(f)
WHERE (f:Function OR f:Method)
    AND coalesce(f.is_entry_point, false) = false
WITH DISTINCT f
OPTIONAL MATCH (decorator_src)-[:DECORATES|ANNOTATES]->(f)
WITH f, count(DISTINCT decorator_src) AS decorator_links
WHERE decorator_links = 0
RETURN count(DISTINCT f) AS total_functions
"""

CYPHER_ANALYSIS_TOTAL_FUNCTIONS_FILTERED = """
MATCH (m:Module {project_name: $project_name})-[:DEFINES|DEFINES_METHOD*0..1]->(f)
WHERE (f:Function OR f:Method)
    AND ($module_paths IS NULL OR m.path IN $module_paths)
    AND coalesce(f.is_entry_point, false) = false
WITH DISTINCT f
OPTIONAL MATCH (decorator_src)-[:DECORATES|ANNOTATES]->(f)
WITH f, count(DISTINCT decorator_src) AS decorator_links
WHERE decorator_links = 0
RETURN count(DISTINCT f) AS total_functions
"""

CYPHER_ANALYSIS_UNUSED_IMPORTS = """
MATCH (m:Module {project_name: $project_name})-[:DEFINES]->(i:Import)
WHERE NOT (i)-[:RESOLVES_IMPORT]->()
RETURN m.path AS path,
             i.import_source AS name,
             i.qualified_name AS qualified_name
"""

CYPHER_ANALYSIS_UNUSED_IMPORTS_FILTERED = """
MATCH (m:Module {project_name: $project_name})-[:DEFINES]->(i:Import)
WHERE ($module_paths IS NULL OR m.path IN $module_paths)
    AND NOT (i)-[:RESOLVES_IMPORT]->()
RETURN m.path AS path,
             i.import_source AS name,
             i.qualified_name AS qualified_name
"""


def wrap_with_unwind(query: str) -> str:
    """
    Wraps a given Cypher query with `UNWIND $batch AS row` for batch operations.

    Args:
        query (str): The core Cypher query to be executed for each row in the batch.

    Returns:
        str: The complete batch query string.
    """
    return f"UNWIND $batch AS row\n{query}"


def build_nodes_by_ids_query(node_ids: list[int]) -> str:
    """
    Builds a query to fetch nodes by a list of their database IDs.

    Args:
        node_ids (list[int]): A list of node IDs to fetch.

    Returns:
        str: The constructed Cypher query string.
    """
    placeholders = ", ".join(f"${i}" for i in range(len(node_ids)))
    return f"""
MATCH (n)
WHERE id(n) IN [{placeholders}]
RETURN id(n) AS node_id, n.qualified_name AS qualified_name,
       labels(n) AS type, n.name AS name
ORDER BY n.qualified_name
"""


def build_context_nodes_query(node_ids: list[int]) -> str:
    placeholders = ", ".join(f"${i}" for i in range(len(node_ids)))
    return f"""
MATCH (n)
WHERE id(n) IN [{placeholders}]
OPTIONAL MATCH (m:Module)-[*0..1]-(n)
WITH n, collect(DISTINCT m.path) AS paths
RETURN id(n) AS node_id,
       n.qualified_name AS qualified_name,
       labels(n) AS type,
       n.name AS name,
       n.docstring AS docstring,
       n.start_line AS start_line,
       n.end_line AS end_line,
       coalesce(n.path, paths[0]) AS path,
       coalesce(n.signature, n.signature_lite, '') AS signature,
       coalesce(n.visibility, '') AS visibility,
       coalesce(n.module_qn, '') AS module_qn,
       coalesce(n.namespace, '') AS namespace,
       coalesce(n.symbol_kind, toLower(labels(n)[0]), '') AS symbol_kind,
       coalesce(n.pagerank, 0.0) AS pagerank,
       coalesce(n.community_id, -1) AS community_id,
       coalesce(n.has_cycle, false) AS has_cycle,
       coalesce(n.in_call_count, 0) AS in_call_count,
       coalesce(n.out_call_count, 0) AS out_call_count,
       coalesce(n.dead_code_score, 0.0) AS dead_code_score,
       coalesce(n.is_reachable, true) AS is_reachable,
       n.parameters AS parameters
ORDER BY n.qualified_name
"""


def build_neighbor_nodes_query(
    node_ids: list[int], hops: int, rel_types: list[str], limit: int
) -> str:
    placeholders = ", ".join(f"${i}" for i in range(len(node_ids)))
    rel_filter = "|".join(rel_types)
    rel_clause = f":{rel_filter}" if rel_filter else ""
    return f"""
MATCH (seed)
WHERE id(seed) IN [{placeholders}]
MATCH (seed)-[{rel_clause}*1..{hops}]-(neighbor)
RETURN DISTINCT id(neighbor) AS node_id
LIMIT {limit}
"""


def build_constraint_query(label: str, prop: str) -> str:
    """
    Builds a query to create a uniqueness constraint on a node label and property.

    Args:
        label (str): The node label.
        prop (str): The property that must be unique.

    Returns:
        str: The `CREATE CONSTRAINT` query string.
    """
    return f"CREATE CONSTRAINT ON (n:{label}) ASSERT n.{prop} IS UNIQUE;"


def build_index_query(label: str, prop: str) -> str:
    """
    Builds a query to create an index on a node label and property.

    Args:
        label (str): The node label.
        prop (str): The property to index.

    Returns:
        str: The `CREATE INDEX` query string.
    """
    return f"CREATE INDEX ON :{label}({prop});"


def build_merge_node_query(label: str, id_key: str) -> str:
    """
    Builds a query to merge a node based on a unique property and set its other properties.

    Args:
        label (str): The node label.
        id_key (str): The unique property key to merge on.

    Returns:
        str: The `MERGE` query string for the node.
    """
    return f"MERGE (n:{label} {{{id_key}: row.id}})\nSET n += row.props"


def build_merge_relationship_query(
    from_label: str,
    from_key: str,
    rel_type: str,
    to_label: str,
    to_key: str,
    has_props: bool = False,
) -> str:
    """
    Builds a query to merge a relationship between two nodes.

    Args:
        from_label (str): The label of the source node.
        from_key (str): The unique property key of the source node.
        rel_type (str): The type of the relationship.
        to_label (str): The label of the target node.
        to_key (str): The unique property key of the target node.
        has_props (bool): Whether the relationship has properties to set.

    Returns:
        str: The `MERGE` query string for the relationship.
    """
    query = (
        f"MATCH (a:{from_label} {{{from_key}: row.from_val}}), "
        f"(b:{to_label} {{{to_key}: row.to_val}})\n"
        f"MERGE (a)-[r:{rel_type}]->(b)\n"
    )
    query += CYPHER_SET_PROPS_RETURN_COUNT if has_props else CYPHER_RETURN_COUNT
    return query
