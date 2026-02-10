"""
This module centralizes all Cypher queries used in the application.

It contains both static query strings for common operations and functions to
dynamically build more complex queries. This approach helps in maintaining
and debugging database interactions.

The queries are organized into several categories:
-   Database management queries (e.g., deleting data, listing projects).
-   Example queries used in prompts to guide the LLM.
-   Data export queries for nodes and relationships.
-   Functions for building dynamic queries, such as creating constraints,
    indexes, and MERGE statements for nodes and relationships.
"""

from codebase_rag.core.constants import CYPHER_DEFAULT_LIMIT

# --- Database Management Queries ---

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

# --- Example Queries for LLM Prompts ---

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

# --- Data Export and Retrieval Queries ---

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


# --- Dynamic Query Builders ---


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
