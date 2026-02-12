from __future__ import annotations

import os
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
            "CALL mg.procedures() YIELD name WITH name WHERE name STARTS WITH 'pagerank' RETURN name LIMIT 1",
            "CALL mg.procedures() YIELD name WITH name WHERE name STARTS WITH 'community_detection' RETURN name LIMIT 1",
            "CALL mg.procedures() YIELD name WITH name WHERE name STARTS WITH 'cycles' RETURN name LIMIT 1",
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
            SET node.pagerank = rank
            RETURN count(node) AS nodes_updated;
            """

            result = self.query_engine.fetch_all(write_query)
            if result:
                logger.info(
                    f"MAGE PageRank completed: {result[0].get('nodes_updated', 0)} nodes updated."
                )
            else:
                logger.info("MAGE PageRank completed.")

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
            SET node.community_id = community_id
            RETURN count(node) AS nodes_updated;
            """

            result = self.query_engine.fetch_all(write_query)
            if result:
                logger.info(
                    f"MAGE Community Detection completed: {result[0].get('nodes_updated', 0)} nodes updated."
                )
            else:
                logger.info("MAGE Community Detection completed.")

        except Exception as e:
            logger.error(f"Failed to run MAGE Community Detection: {e}")

    def detect_cycles(self) -> None:
        """
        Detects cycles in dependency graph and marks nodes participating in cycles.
        Sets 'has_cycle = true' on nodes that are part of a loop.

        Can be configured with:
        - CODEGRAPH_CYCLE_LIMIT: Max number of cycles to process (default: 100)
        - CODEGRAPH_CYCLE_MIN_SIZE: Minimum cycle size to consider (default: 2)
        """
        logger.info("Running MAGE Cycle Detection...")

        try:
            cycle_limit = int(os.getenv("CODEGRAPH_CYCLE_LIMIT", "100"))
            min_cycle_size = int(os.getenv("CODEGRAPH_CYCLE_MIN_SIZE", "2"))

            reset_query = """
            MATCH (n) WHERE n.has_cycle = true
            REMOVE n.has_cycle, n.cycle_size;
            """
            self.query_engine.execute_write(reset_query)

            write_query = f"""
            CALL cycles.get()
            YIELD cycle
            WHERE cycle IS NOT NULL AND size(cycle) >= {min_cycle_size}
            WITH cycle, size(cycle) AS cycle_size
            ORDER BY cycle_size DESC
            LIMIT {cycle_limit}
            UNWIND cycle AS n
            SET n.has_cycle = true, n.cycle_size = cycle_size
            RETURN count(DISTINCT n) AS nodes_marked,
                   max(cycle_size) AS max_cycle_size,
                   min(cycle_size) AS min_cycle_size,
                   avg(cycle_size) AS avg_cycle_size;
            """

            result = self.query_engine.fetch_all(write_query)
            if result and result[0].get("nodes_marked", 0) > 0:
                stats = result[0]
                logger.info(
                    f"MAGE Cycle Detection completed: {stats['nodes_marked']} nodes marked "
                    f"(cycle sizes: {stats['min_cycle_size']}-{stats['max_cycle_size']}, "
                    f"avg: {stats['avg_cycle_size']:.1f}, limit: {cycle_limit})"
                )
            else:
                logger.info("MAGE Cycle Detection completed: No cycles found.")

        except Exception as e:
            logger.error(f"Failed to run MAGE Cycle Detection: {e}")
            logger.warning(
                "Consider disabling cycle detection with CODEGRAPH_MAGE_CYCLES=0 for large graphs."
            )

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
        cycles_enabled = os.getenv("CODEGRAPH_MAGE_CYCLES", "1").lower() in {
            "1",
            "true",
            "yes",
        }
        if cycles_enabled:
            self.detect_cycles()
        else:
            logger.info("Skipping MAGE cycle detection: disabled by config.")
