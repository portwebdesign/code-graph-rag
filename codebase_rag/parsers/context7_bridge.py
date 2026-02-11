from __future__ import annotations

import asyncio
from typing import cast

from loguru import logger

from ..services.context7_client import Context7Client
from ..services.protocols import QueryProtocol


class Context7Bridge:
    """
    Bridges the gap between parsed External Packages/Modules and Context7 Library nodes.
    Creates structural relationships (REQUIRES_DOC) to allow graph traversal to documentation.

    Attributes:
        ingestor (QueryProtocol): The query protocol interface for graph operations.
        client (Context7Client): Client for interacting with Context7 service.
    """

    def __init__(self, ingestor: QueryProtocol):
        """
        Initialize the Context7Bridge.

        Args:
            ingestor (QueryProtocol): The query protocol interface.
        """
        self.ingestor = ingestor
        self.client = Context7Client()

    def run(self) -> None:
        """
        Synchronous entry point to run the bridging process.
        """
        if not self.client.is_configured():
            logger.debug("Context7 not configured, skipping semantic bridging.")
            return

        try:
            asyncio.run(self._bridge_external_entities())
        except Exception as e:
            logger.error(f"Context7 bridging failed: {e}")

    async def _bridge_external_entities(self) -> None:
        """
        Identifies unlinked external entities and resolves them via Context7.
        """
        query = """
        MATCH (m)
        WHERE (m:Module AND m.is_external = true) OR (m:ExternalPackage)
        AND NOT (m)-[:REQUIRES_DOC]->(:Library)
        RETURN m.name AS name, labels(m) AS labels, m.qualified_name AS qn
        LIMIT 50
        """

        results = self.ingestor.fetch_all(query)
        if not results:
            return

        logger.info(
            f"Context7 Bridging: Found {len(results)} unlinked external entities."
        )

        for row in results:
            name = row.get("name")
            qn = row.get("qn")

            if not isinstance(name, str) or not name:
                continue

            if not isinstance(qn, str) or not qn:
                continue

            if len(name) < 2:
                continue

            await self._process_entity(name, qn, cast(list[str], row.get("labels", [])))

    async def _process_entity(self, name: str, qn: str, labels: list[str]) -> None:
        """
        Process a single external entity to link it with a library ID.

        Args:
            name (str): Name of the entity.
            qn (str): Qualified name of the entity.
            labels (list[str]): List of labels associated with the entity.
        """
        resolved = await self.client.resolve_library_id(name)

        library_id = self.client._extract_library_id(resolved)

        if not library_id:
            logger.debug(f"Context7: Could not resolve library for '{name}'")
            return

        self.ingestor.execute_write(
            """
            MERGE (l:Library {name: $name})
            SET l.library_id = $library_id,
                l.source = 'context7'
            """,
            {"name": name, "library_id": library_id},
        )

        start_label = "ExternalPackage" if "ExternalPackage" in labels else "Module"

        self.ingestor.execute_write(
            f"""
            MATCH (source:{start_label} {{qualified_name: $qn}})
            MATCH (target:Library {{name: $name}})
            MERGE (source)-[:REQUIRES_DOC]->(target)
            """,
            {"qn": qn, "name": name},
        )

        logger.info(f"Context7 Linked: {name} ({qn}) -> {library_id}")
