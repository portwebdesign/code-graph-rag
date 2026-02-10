"""
This module provides the `MemgraphIngestor` class, a service responsible for
all interactions with the Memgraph database.

It handles connecting to the database, executing Cypher queries, and ingesting
data in batches. The class uses a context manager pattern for managing the
database connection lifecycle. It also includes buffering mechanisms for nodes
and relationships to optimize write performance by grouping multiple operations
into single batch transactions.

Key functionalities:
-   Connecting to and disconnecting from Memgraph.
-   Executing single and batch Cypher queries.
-   Buffering and flushing nodes and relationships to the database.
-   Ensuring database constraints and indexes for performance.
-   Exporting the entire graph to a dictionary format.
-   Cleaning the database and managing project-specific data.
"""

from __future__ import annotations

import types
from collections import defaultdict
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime

import mgclient  # ty: ignore[unresolved-import]
from loguru import logger

from codebase_rag.core.constants import (
    ERR_SUBSTR_ALREADY_EXISTS,
    ERR_SUBSTR_CONSTRAINT,
    KEY_CREATED,
    KEY_FROM_VAL,
    KEY_NAME,
    KEY_PROJECT_NAME,
    KEY_PROPS,
    KEY_TO_VAL,
    NODE_UNIQUE_CONSTRAINTS,
    REL_TYPE_CALLS,
)
from codebase_rag.data_models.types_defs import (
    BatchParams,
    BatchWrapper,
    CursorProtocol,
    GraphData,
    GraphMetadata,
    NodeBatchRow,
    PropertyDict,
    PropertyValue,
    RelBatchRow,
    ResultRow,
    ResultValue,
)
from codebase_rag.graph_db.cypher_queries import (
    CYPHER_DELETE_ALL,
    CYPHER_DELETE_PROJECT,
    CYPHER_EXPORT_NODES,
    CYPHER_EXPORT_RELATIONSHIPS,
    CYPHER_LIST_PROJECTS,
    build_constraint_query,
    build_index_query,
    build_merge_node_query,
    build_merge_relationship_query,
    wrap_with_unwind,
)

from ..core import logs as ls
from ..infrastructure import exceptions as ex


class MemgraphIngestor:
    """
    Manages connection and data ingestion for a Memgraph database.

    This class provides methods to execute queries, manage constraints, and
    batch-insert nodes and relationships for performance.
    """

    def __init__(self, host: str, port: int, batch_size: int = 1000):
        """
        Initializes the MemgraphIngestor.

        Args:
            host (str): The hostname or IP address of the Memgraph instance.
            port (int): The port number of the Memgraph instance.
            batch_size (int): The number of nodes or relationships to buffer before
                              flushing to the database.
        """
        self._host = host
        self._port = port
        if batch_size < 1:
            raise ValueError(ex.BATCH_SIZE)
        self.batch_size = batch_size
        self.conn: mgclient.Connection | None = None
        self.node_buffer: list[tuple[str, dict[str, PropertyValue]]] = []
        self.relationship_buffer: list[
            tuple[
                tuple[str, str, PropertyValue],
                str,
                tuple[str, str, PropertyValue],
                dict[str, PropertyValue] | None,
            ]
        ] = []

    def __enter__(self) -> MemgraphIngestor:
        """
        Connects to the Memgraph database when entering the context.

        Returns:
            MemgraphIngestor: The connected ingestor instance.
        """
        logger.info(ls.MG_CONNECTING.format(host=self._host, port=self._port))
        self.conn = mgclient.connect(host=self._host, port=self._port)
        self.conn.autocommit = True
        logger.info(ls.MG_CONNECTED)
        return self

    def __exit__(
        self,
        exc_type: type | None,
        exc_val: Exception | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        """
        Flushes all data and closes the connection when exiting the context.

        Args:
            exc_type (type | None): The type of the exception, if any.
            exc_val (Exception | None): The exception instance, if any.
            exc_tb (types.TracebackType | None): The traceback, if any.
        """
        if exc_type:
            logger.exception(ls.MG_EXCEPTION.format(error=exc_val))
        self.flush_all()
        if self.conn:
            self.conn.close()
            logger.info(ls.MG_DISCONNECTED)

    @contextmanager
    def _get_cursor(self) -> Generator[CursorProtocol, None, None]:
        """
        Provides a database cursor within a context manager.

        Yields:
            CursorProtocol: The database cursor.
        """
        if not self.conn:
            raise ConnectionError(ex.CONN)
        cursor: CursorProtocol | None = None
        try:
            cursor = self.conn.cursor()
            yield cursor
        finally:
            if cursor:
                cursor.close()

    def _cursor_to_results(self, cursor: CursorProtocol) -> list[ResultRow]:
        """
        Converts a database cursor's fetched data into a list of dictionaries.

        Args:
            cursor (CursorProtocol): The cursor after a query has been executed.

        Returns:
            list[ResultRow]: A list of rows, where each row is a dictionary.
        """
        if not cursor.description:
            return []
        column_names = [desc.name for desc in cursor.description]
        return [
            dict[str, ResultValue](zip(column_names, row)) for row in cursor.fetchall()
        ]

    def _execute_query(
        self,
        query: str,
        params: dict[str, PropertyValue] | None = None,
    ) -> list[ResultRow]:
        """
        Executes a single Cypher query.

        Args:
            query (str): The Cypher query to execute.
            params (dict[str, PropertyValue] | None): Parameters for the query.

        Returns:
            list[ResultRow]: The query results as a list of dictionaries.
        """
        params = params or {}
        with self._get_cursor() as cursor:
            try:
                cursor.execute(query, params)
                return self._cursor_to_results(cursor)
            except Exception as e:
                if (
                    ERR_SUBSTR_ALREADY_EXISTS not in str(e).lower()
                    and ERR_SUBSTR_CONSTRAINT not in str(e).lower()
                ):
                    logger.error(ls.MG_CYPHER_ERROR.format(error=e))
                    logger.error(ls.MG_CYPHER_QUERY.format(query=query))
                    logger.error(ls.MG_CYPHER_PARAMS.format(params=params))
                raise

    def _execute_batch(self, query: str, params_list: Sequence[BatchParams]) -> None:
        """
        Executes a batch query using `UNWIND`.

        Args:
            query (str): The core Cypher query to run for each item in the batch.
            params_list (Sequence[BatchParams]): A list of parameter dictionaries.
        """
        if not self.conn or not params_list:
            return
        cursor = None
        try:
            cursor = self.conn.cursor()
            cursor.execute(wrap_with_unwind(query), BatchWrapper(batch=params_list))
        except Exception as e:
            if ERR_SUBSTR_ALREADY_EXISTS not in str(e).lower():
                logger.error(ls.MG_BATCH_ERROR.format(error=e))
                logger.error(ls.MG_CYPHER_QUERY.format(query=query))
                if len(params_list) > 10:
                    logger.error(
                        ls.MG_BATCH_PARAMS_TRUNCATED.format(
                            count=len(params_list), params=params_list[:10]
                        )
                    )
                else:
                    logger.error(ls.MG_CYPHER_PARAMS.format(params=params_list))
            raise
        finally:
            if cursor:
                cursor.close()

    def _execute_batch_with_return(
        self, query: str, params_list: Sequence[BatchParams]
    ) -> list[ResultRow]:
        """
        Executes a batch query and returns the results.

        Args:
            query (str): The core Cypher query.
            params_list (Sequence[BatchParams]): A list of parameter dictionaries.

        Returns:
            list[ResultRow]: The query results.
        """
        if not self.conn or not params_list:
            return []
        cursor = None
        try:
            cursor = self.conn.cursor()
            cursor.execute(wrap_with_unwind(query), BatchWrapper(batch=params_list))
            return self._cursor_to_results(cursor)
        except Exception as e:
            logger.error(ls.MG_BATCH_ERROR.format(error=e))
            logger.error(ls.MG_CYPHER_QUERY.format(query=query))
            raise
        finally:
            if cursor:
                cursor.close()

    def clean_database(self) -> None:
        """Deletes all nodes and relationships from the database."""
        logger.info(ls.MG_CLEANING_DB)
        self._execute_query(CYPHER_DELETE_ALL)
        logger.info(ls.MG_DB_CLEANED)

    def list_projects(self) -> list[str]:
        """
        Lists all project names in the database.

        Returns:
            list[str]: A list of project names.
        """
        result = self.fetch_all(CYPHER_LIST_PROJECTS)
        return [str(r[KEY_NAME]) for r in result]

    def delete_project(self, project_name: str) -> None:
        """
        Deletes a project and all its associated data.

        Args:
            project_name (str): The name of the project to delete.
        """
        logger.info(ls.MG_DELETING_PROJECT.format(project_name=project_name))
        self._execute_query(CYPHER_DELETE_PROJECT, {KEY_PROJECT_NAME: project_name})
        logger.info(ls.MG_PROJECT_DELETED.format(project_name=project_name))

    def ensure_constraints(self) -> None:
        """Ensures all unique constraints are created in the database."""
        logger.info(ls.MG_ENSURING_CONSTRAINTS)
        for label, prop in NODE_UNIQUE_CONSTRAINTS.items():
            try:
                self._execute_query(build_constraint_query(label, prop))
            except Exception:
                pass
        logger.info(ls.MG_CONSTRAINTS_DONE)
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        """Ensures all necessary indexes are created for performance."""
        logger.info(ls.MG_ENSURING_INDEXES)
        for label, prop in NODE_UNIQUE_CONSTRAINTS.items():
            try:
                self._execute_query(build_index_query(label, prop))
            except Exception:
                pass
        logger.info(ls.MG_INDEXES_DONE)

    def ensure_node_batch(
        self, label: str, properties: dict[str, PropertyValue]
    ) -> None:
        """
        Adds a node to the buffer for batch ingestion.

        Args:
            label (str): The label of the node.
            properties (dict[str, PropertyValue]): The properties of the node.
        """
        self.node_buffer.append((label, properties))
        if len(self.node_buffer) >= self.batch_size:
            logger.debug(ls.MG_NODE_BUFFER_FLUSH.format(size=self.batch_size))
            self.flush_nodes()

    def ensure_relationship_batch(
        self,
        from_spec: tuple[str, str, PropertyValue],
        rel_type: str,
        to_spec: tuple[str, str, PropertyValue],
        properties: dict[str, PropertyValue] | None = None,
    ) -> None:
        """
        Adds a relationship to the buffer for batch ingestion.

        Args:
            from_spec (tuple): A tuple for the source node (label, key, value).
            rel_type (str): The type of the relationship.
            to_spec (tuple): A tuple for the target node (label, key, value).
            properties (dict | None): Optional properties for the relationship.
        """
        from_label, from_key, from_val = from_spec
        to_label, to_key, to_val = to_spec
        self.relationship_buffer.append(
            (
                (from_label, from_key, from_val),
                rel_type,
                (to_label, to_key, to_val),
                properties,
            )
        )
        if len(self.relationship_buffer) >= self.batch_size:
            logger.debug(ls.MG_REL_BUFFER_FLUSH.format(size=self.batch_size))
            self.flush_nodes()
            self.flush_relationships()

    def flush_nodes(self) -> None:
        """Flushes the buffered nodes to the database."""
        if not self.node_buffer:
            return

        buffer_size = len(self.node_buffer)
        nodes_by_label: defaultdict[str, list[dict[str, PropertyValue]]] = defaultdict(
            list
        )
        for label, props in self.node_buffer:
            nodes_by_label[label].append(props)
        flushed_total = 0
        skipped_total = 0
        for label, props_list in nodes_by_label.items():
            if not props_list:
                continue
            id_key = NODE_UNIQUE_CONSTRAINTS.get(label)
            if not id_key:
                logger.warning(ls.MG_NO_CONSTRAINT.format(label=label))
                skipped_total += len(props_list)
                continue

            batch_rows: list[NodeBatchRow] = []
            for props in props_list:
                if id_key not in props:
                    logger.warning(
                        ls.MG_MISSING_PROP.format(label=label, key=id_key, props=props)
                    )
                    skipped_total += 1
                    continue
                row_props: PropertyDict = {
                    k: v for k, v in props.items() if k != id_key
                }
                batch_rows.append(NodeBatchRow(id=props[id_key], props=row_props))

            if not batch_rows:
                continue

            flushed_total += len(batch_rows)

            query = build_merge_node_query(label, id_key)
            self._execute_batch(query, batch_rows)
        logger.info(
            ls.MG_NODES_FLUSHED.format(flushed=flushed_total, total=buffer_size)
        )
        if skipped_total:
            logger.info(ls.MG_NODES_SKIPPED.format(count=skipped_total))
        self.node_buffer.clear()

    def flush_relationships(self) -> None:
        """Flushes the buffered relationships to the database."""
        if not self.relationship_buffer:
            return

        rels_by_pattern: defaultdict[
            tuple[str, str, str, str, str], list[RelBatchRow]
        ] = defaultdict(list)
        for from_node, rel_type, to_node, props in self.relationship_buffer:
            pattern = (from_node[0], from_node[1], rel_type, to_node[0], to_node[1])
            rels_by_pattern[pattern].append(
                RelBatchRow(from_val=from_node[2], to_val=to_node[2], props=props or {})
            )

        total_attempted = 0
        total_successful = 0

        for pattern, params_list in rels_by_pattern.items():
            from_label, from_key, rel_type, to_label, to_key = pattern
            has_props = any(p[KEY_PROPS] for p in params_list)
            query = build_merge_relationship_query(
                from_label, from_key, rel_type, to_label, to_key, has_props
            )

            total_attempted += len(params_list)
            results = self._execute_batch_with_return(query, params_list)
            batch_successful = 0
            for r in results:
                created = r.get(KEY_CREATED, 0)
                if isinstance(created, int):
                    batch_successful += created
            total_successful += batch_successful

            if rel_type == REL_TYPE_CALLS:
                failed = len(params_list) - batch_successful
                if failed > 0:
                    logger.warning(ls.MG_CALLS_FAILED.format(count=failed))
                    for i, sample in enumerate(params_list[:3]):
                        logger.warning(
                            ls.MG_CALLS_SAMPLE.format(
                                index=i + 1,
                                from_label=from_label,
                                from_val=sample[KEY_FROM_VAL],
                                to_label=to_label,
                                to_val=sample[KEY_TO_VAL],
                            )
                        )

        logger.info(
            ls.MG_RELS_FLUSHED.format(
                total=len(self.relationship_buffer),
                success=total_successful,
                failed=total_attempted - total_successful,
            )
        )
        self.relationship_buffer.clear()

    def flush_all(self) -> None:
        """Flushes all buffered nodes and relationships to the database."""
        logger.info(ls.MG_FLUSH_START)
        self.flush_nodes()
        self.flush_relationships()
        logger.info(ls.MG_FLUSH_COMPLETE)

    def fetch_all(
        self, query: str, params: dict[str, PropertyValue] | None = None
    ) -> list[ResultRow]:
        """
        Executes a read query and fetches all results.

        Args:
            query (str): The Cypher query to execute.
            params (dict | None): Parameters for the query.

        Returns:
            list[ResultRow]: The query results.
        """
        logger.debug(ls.MG_FETCH_QUERY.format(query=query, params=params))
        return self._execute_query(query, params)

    def execute_write(
        self, query: str, params: dict[str, PropertyValue] | None = None
    ) -> None:
        """
        Executes a write query.

        Args:
            query (str): The Cypher query to execute.
            params (dict | None): Parameters for the query.
        """
        logger.debug(ls.MG_WRITE_QUERY.format(query=query, params=params))
        self._execute_query(query, params)

    def export_graph_to_dict(self) -> GraphData:
        """
        Exports the entire graph to a dictionary.

        Returns:
            GraphData: A dictionary containing all nodes, relationships, and metadata.
        """
        logger.info(ls.MG_EXPORTING)

        nodes_data = self.fetch_all(CYPHER_EXPORT_NODES)
        relationships_data = self.fetch_all(CYPHER_EXPORT_RELATIONSHIPS)

        metadata = GraphMetadata(
            total_nodes=len(nodes_data),
            total_relationships=len(relationships_data),
            exported_at=self._get_current_timestamp(),
        )

        logger.info(
            ls.MG_EXPORTED.format(nodes=len(nodes_data), rels=len(relationships_data))
        )
        return GraphData(
            nodes=nodes_data,
            relationships=relationships_data,
            metadata=metadata,
        )

    def _get_current_timestamp(self) -> str:
        """
        Gets the current UTC timestamp in ISO format.

        Returns:
            str: The ISO-formatted timestamp string.
        """
        return datetime.now(UTC).isoformat()
