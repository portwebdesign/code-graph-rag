from __future__ import annotations

from typing import Any, Protocol

from loguru import logger


class GraphQueryProtocol(Protocol):
    def execute_write(
        self, query: str, params: dict[str, Any] | None = None
    ) -> None: ...
    def fetch_all(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]: ...


class GraphAlgorithms:
    """
    Wrapper for MAGE (Memgraph Advanced Graph Extensions) algorithms.
    This tool is strictly for maintenance/analytics hooks, not for general agent tool use.
    """

    def __init__(self, query_engine: GraphQueryProtocol):
        self.query_engine = query_engine
        self._mage_checked = False
        self._mage_available = False

    def _is_mage_available(self) -> bool:
        if self._mage_checked:
            return self._mage_available
        self._mage_checked = True
        last_error: Exception | None = None
        checks = (
            "CALL mage.procedures() YIELD name RETURN name LIMIT 1",
            "CALL mg.procedures() YIELD name WHERE name STARTS WITH 'pagerank' RETURN name LIMIT 1",
            "CALL mg.procedures() YIELD name WHERE name STARTS WITH 'community_detection' RETURN name LIMIT 1",
            "CALL mg.procedures() YIELD name WHERE name STARTS WITH 'cycles' RETURN name LIMIT 1",
        )
        for query in checks:
            try:
                result = self.query_engine.fetch_all(query)
                if result:
                    self._mage_available = True
                    return True
            except Exception as exc:
                last_error = exc
                continue
        if last_error is not None:
            logger.warning("MAGE procedures not available: {}", last_error)
        self._mage_available = False
        return False

    def run_pagerank(self) -> None:
        """
        Executes PageRank on the graph and updates nodes with 'pagerank' property.
        Focuses on structural dependencies (CALLS, INHERITS, IMPORTS).
        """
        logger.info("Running MAGE PageRank Analysis...")

        try:
            write_query = """
            CALL pagerank.get()
            YIELD node, rank
            SET node.pagerank = rank;
            """

            self.query_engine.execute_write(write_query)
            logger.info("MAGE PageRank completed. Properties updated.")

        except Exception as e:
            logger.error(f"Failed to run MAGE PageRank: {e}")

    def detect_communities(self) -> None:
        """
        Executes Leiden community detection and updates nodes with 'community_id' property.
        """
        logger.info("Running MAGE Leiden Community Detection...")

        try:
            write_query = """
            CALL community_detection.get()
            YIELD node, community_id
            SET node.community_id = community_id;
            """

            self.query_engine.execute_write(write_query)
            logger.info("MAGE Community Detection completed. Properties updated.")

        except Exception as e:
            logger.error(f"Failed to run MAGE Community Detection: {e}")

    def detect_cycles(self) -> None:
        """
        Detects cycles in dependency graph and marks nodes participating in cycles.
        Sets 'has_cycle = true' on nodes that are part of a loop.
        """
        logger.info("Running MAGE Cycle Detection...")

        try:
            reset_query = """
            MATCH (n) WHERE n.has_cycle = true
            REMOVE n.has_cycle;
            """
            self.query_engine.execute_write(reset_query)

            write_query = """
            CALL cycles.get()
            YIELD cycle
            UNWIND cycle AS n
            SET n.has_cycle = true;
            """

            self.query_engine.execute_write(write_query)
            logger.info("MAGE Cycle Detection completed. Properties updated.")

        except Exception as e:
            logger.error(f"Failed to run MAGE Cycle Detection: {e}")

    def run_all(self, has_changes: bool = True) -> None:
        """Runs all registered graph analysis algorithms."""
        if not has_changes:
            logger.info("Skipping MAGE graph algorithms: no changes detected.")
            return
        if not self._is_mage_available():
            logger.info("Skipping MAGE graph algorithms: MAGE not available.")
            return
        self.run_pagerank()
        self.detect_communities()
        self.detect_cycles()
