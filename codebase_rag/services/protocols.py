"""
This module defines the core protocols for services within the application.

Protocols are used to define a common interface that different classes can
implement. This promotes loose coupling and allows for interchangeable components,
such as swapping out a graph database implementation without changing the services
that use it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from codebase_rag.data_models.types_defs import PropertyDict, PropertyValue, ResultRow


@runtime_checkable
class IngestorProtocol(Protocol):
    """
    A protocol for services that ingest data into a database.

    This protocol defines the interface for adding nodes and relationships to a graph,
    typically in batches for efficiency.
    """

    def ensure_node_batch(self, label: str, properties: PropertyDict) -> None:
        """
        Adds a node to the current batch to be created or merged.

        This method should be idempotent; if a node with the same unique properties
        already exists, it should be updated (merged), not duplicated.

        Args:
            label (str): The label of the node (e.g., "Function", "Class").
            properties (PropertyDict): A dictionary of properties for the node.
        """
        ...

    def ensure_relationship_batch(
        self,
        from_spec: tuple[str, str, PropertyValue],
        rel_type: str,
        to_spec: tuple[str, str, PropertyValue],
        properties: PropertyDict | None = None,
    ) -> None:
        """
        Adds a relationship to the current batch to be created.

        This method should also be idempotent.

        Args:
            from_spec (tuple): A tuple specifying the start node (label, key, value).
            rel_type (str): The type of the relationship (e.g., "CALLS", "DEFINES").
            to_spec (tuple): A tuple specifying the end node (label, key, value).
            properties (PropertyDict | None): Optional properties for the relationship.
        """
        ...

    def flush_all(self) -> None:
        """
        Writes all pending nodes and relationships in the current batch to the database.

        This method is called periodically or at the end of a process to commit
        the batched operations.
        """
        ...


@runtime_checkable
class QueryProtocol(Protocol):
    """
    A protocol for services that query a database.

    This defines a standard interface for executing read and write queries.
    """

    def fetch_all(
        self, query: str, params: PropertyDict | None = None
    ) -> list[ResultRow]:
        """
        Executes a read query and fetches all resulting rows.

        Args:
            query (str): The query string to execute (e.g., a Cypher query).
            params (PropertyDict | None): A dictionary of parameters for the query.

        Returns:
            A list of `ResultRow` objects, where each object represents a row
            from the query result.
        """
        ...

    def execute_write(self, query: str, params: PropertyDict | None = None) -> None:
        """
        Executes a write query.

        This is used for operations that modify the database but do not return data,
        such as `CREATE`, `MERGE`, or `DELETE`.

        Args:
            query (str): The write query string to execute.
            params (PropertyDict | None): A dictionary of parameters for the query.
        """
        ...
