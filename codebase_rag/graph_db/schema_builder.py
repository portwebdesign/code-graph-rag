"""
This module is responsible for building a human-readable text representation
of the graph schema.

It uses the schema definitions from `types_defs.py` to generate a formatted
string that describes the node labels, their properties, and the relationships
between them. This text-based schema is primarily intended to be used in prompts
for Large Language Models (LLMs), providing them with the necessary context to
understand the graph structure and generate valid Cypher queries.

The main functions build sections for node labels and relationships, which are
then combined into a single, comprehensive schema definition string.
"""

from codebase_rag.data_models.types_defs import (
    NODE_SCHEMAS,
    RELATIONSHIP_SCHEMAS,
    NodeSchema,
    RelationshipSchema,
)


def _format_node_schema(schema: NodeSchema) -> str:
    """
    Formats a single node schema into a human-readable string.

    Example output: "- Project: {name: string}"

    Args:
        schema (NodeSchema): The node schema to format.

    Returns:
        str: The formatted string representation.
    """
    return f"- {schema.label}: {schema.properties}"


def _format_relationship_schema(schema: RelationshipSchema) -> str:
    """
    Formats a single relationship schema into a human-readable string.

    Example output: "- (Project|Package|Folder) -[:CONTAINS_FILE]-> (File)"

    Args:
        schema (RelationshipSchema): The relationship schema to format.

    Returns:
        str: The formatted string representation.
    """
    sources = "|".join(str(s) for s in schema.sources)
    targets = "|".join(str(t) for t in schema.targets)
    if len(schema.sources) > 1:
        sources = f"({sources})"
    if len(schema.targets) > 1:
        targets = f"({targets})"
    return f"- {sources} -[:{schema.rel_type}]-> {targets}"


def build_node_labels_section() -> str:
    """
    Builds the complete "Node Labels" section of the schema text.

    Returns:
        str: A formatted string listing all node labels and their properties.
    """
    lines = ["Node Labels and Their Key Properties:"]
    lines.extend(_format_node_schema(schema) for schema in NODE_SCHEMAS)
    return "\n".join(lines)


def build_relationships_section() -> str:
    """
    Builds the complete "Relationships" section of the schema text.

    Returns:
        str: A formatted string listing all relationship types and their
             source/target nodes.
    """
    lines = ["Relationships (source)-[REL_TYPE]->(target):"]
    lines.extend(_format_relationship_schema(schema) for schema in RELATIONSHIP_SCHEMAS)
    return "\n".join(lines)


def build_graph_schema_text() -> str:
    """
    Constructs the full graph schema text by combining the node and relationship sections.

    Returns:
        str: The complete, formatted graph schema as a single string.
    """
    return f"""{build_node_labels_section()}

{build_relationships_section()}"""


# A constant holding the generated graph schema text for easy import and use.
GRAPH_SCHEMA_DEFINITION = build_graph_schema_text()
