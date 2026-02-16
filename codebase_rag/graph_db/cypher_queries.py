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
"""

CYPHER_DELETE_DYNAMIC_EDGES_BY_PATH = """
MATCH (m:Module {path: $path})
OPTIONAL MATCH (m)-[:DEFINES|DEFINES_METHOD*0..5]->(defined)
WITH collect(DISTINCT m) + collect(DISTINCT defined) AS nodes
UNWIND nodes AS node
MATCH (node)-[r:CALLS|IMPORTS|EXPORTS|EXPORTS_MODULE|IMPLEMENTS_MODULE|INHERITS|IMPLEMENTS|OVERRIDES|RETURNS_TYPE|PARAMETER_TYPE|CAUGHT_BY|THROWS|DECORATES|ANNOTATES|REQUIRES_LIBRARY|DEPENDS_ON|DEPENDS_ON_EXTERNAL|HAS_ENDPOINT|ROUTES_TO_CONTROLLER|ROUTES_TO_ACTION|RENDERS_VIEW|USES_MIDDLEWARE|REGISTERS_SERVICE|ELOQUENT_RELATION|HOOKS|REGISTERS_BLOCK|USES_ASSET|USES_UTILITY|RESOLVES_IMPORT|USES_COMPONENT|HANDLES_ERROR|MUTATES_STATE|HAS_PARAMETER|HAS_TYPE_PARAMETER|EMBEDS|REQUESTS_ENDPOINT|USES_HANDLER|USES_SERVICE|PROVIDES_SERVICE]-()
DELETE r
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
RETURN n.name AS name, n.start_line AS start, n.end_line AS end, m.path AS path, n.docstring AS docstring
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
WITH f, min(m.path) AS path
WITH f, path,
     size([()-[:CALLS]->(f) | 1]) AS call_in_degree,
     size([(f)-[:CALLS]->() | 1]) AS out_call_count,
     CASE WHEN f.name IN $entry_names THEN true ELSE false END AS is_entrypoint_name,
     CASE WHEN ANY(d IN coalesce(f.decorators, []) WHERE d IN $decorators) THEN true ELSE false END AS has_entry_decorator
WHERE call_in_degree = 0
RETURN DISTINCT f.qualified_name AS qualified_name,
                f.name AS name,
                path AS path,
                f.start_line AS start_line,
                labels(f)[0] AS label,
                call_in_degree,
                out_call_count,
                is_entrypoint_name,
                has_entry_decorator,
                0 AS decorator_links,
                0 AS registration_links,
                0 AS imported_by_cli_links,
                0 AS config_reference_links,
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
WITH f, min(m.path) AS path
WITH f, path,
     size([()-[:CALLS]->(f) | 1]) AS call_in_degree,
     size([(f)-[:CALLS]->() | 1]) AS out_call_count,
     CASE WHEN f.name IN $entry_names THEN true ELSE false END AS is_entrypoint_name,
     CASE WHEN ANY(d IN coalesce(f.decorators, []) WHERE d IN $decorators) THEN true ELSE false END AS has_entry_decorator
WHERE call_in_degree = 0
RETURN DISTINCT f.qualified_name AS qualified_name,
                f.name AS name,
                path AS path,
                f.start_line AS start_line,
                labels(f)[0] AS label,
                call_in_degree,
                out_call_count,
                is_entrypoint_name,
                has_entry_decorator,
                0 AS decorator_links,
                0 AS registration_links,
                0 AS imported_by_cli_links,
                0 AS config_reference_links,
                coalesce(f.decorators, []) AS decorators,
                coalesce(f.is_exported, false) AS is_exported
ORDER BY path, start_line
LIMIT $dead_code_limit
"""

CYPHER_ANALYSIS_TOTAL_FUNCTIONS = """
MATCH (m:Module {project_name: $project_name})-[:DEFINES|DEFINES_METHOD*0..1]->(f)
WHERE (f:Function OR f:Method)
RETURN count(DISTINCT f) AS total_functions
"""

CYPHER_ANALYSIS_TOTAL_FUNCTIONS_FILTERED = """
MATCH (m:Module {project_name: $project_name})-[:DEFINES|DEFINES_METHOD*0..1]->(f)
WHERE (f:Function OR f:Method)
    AND ($module_paths IS NULL OR m.path IN $module_paths)
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
