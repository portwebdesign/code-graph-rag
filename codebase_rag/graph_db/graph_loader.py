"""
This module provides the `GraphLoader` class, which is responsible for loading
a code graph from a JSON file into an in-memory representation.

The JSON file is expected to have a specific structure containing nodes,
relationships, and metadata. The `GraphLoader` parses this file and builds
in-memory indexes to allow for efficient querying of the graph data.

Key functionalities include:
-   Loading graph data from a specified JSON file.
-   Providing access to nodes and relationships.
-   Indexing nodes by ID, label, and properties for fast lookups.
-   Offering methods to query nodes and their relationships.
-   Generating a summary of the graph's contents.

The main class, `GraphLoader`, uses lazy loading for the graph data, and the
`@ensure_loaded` decorator ensures that data is loaded before any operations
are performed. A convenience function `load_graph` is also provided for a
more direct way to load and get a `GraphLoader` instance.
"""

import json
from collections import Counter, defaultdict
from pathlib import Path

from loguru import logger

from codebase_rag.core import constants as cs
from codebase_rag.core import logs as ls
from codebase_rag.data_models.models import GraphNode, GraphRelationship
from codebase_rag.data_models.types_defs import (
    GraphData,
    GraphMetadata,
    GraphSummary,
    PropertyValue,
)
from codebase_rag.infrastructure import exceptions as ex
from codebase_rag.infrastructure.decorators import ensure_loaded


class GraphLoader:
    """Loads and provides access to a code graph from a JSON file.

    This class handles the loading of graph data, builds indexes for efficient
    access, and provides methods to query the graph structure.

    Attributes:
        file_path (Path): The path to the graph JSON file.
    """

    def __init__(self, file_path: str):
        """Initializes the GraphLoader with the path to the graph file.

        Args:
            file_path (str): The path to the JSON graph file.
        """
        self.file_path = Path(file_path)
        self._data: GraphData | None = None
        self._nodes: list[GraphNode] | None = None
        self._relationships: list[GraphRelationship] | None = None

        self._nodes_by_id: dict[int, GraphNode] = {}
        self._nodes_by_label: defaultdict[str, list[GraphNode]] = defaultdict(list)
        self._outgoing_rels: defaultdict[int, list[GraphRelationship]] = defaultdict(
            list
        )
        self._incoming_rels: defaultdict[int, list[GraphRelationship]] = defaultdict(
            list
        )
        self._property_indexes: dict[str, dict[PropertyValue, list[GraphNode]]] = {}

    def _ensure_loaded(self) -> None:
        """Ensures that the graph data has been loaded from the file."""
        if self._data is None:
            self.load()

    def load(self) -> None:
        """Loads the graph data from the JSON file and builds indexes.

        Raises:
            FileNotFoundError: If the graph file does not exist.
            RuntimeError: If the data fails to load from the file.
        """
        if not self.file_path.exists():
            raise FileNotFoundError(ex.GRAPH_FILE_NOT_FOUND.format(path=self.file_path))

        logger.info(ls.LOADING_GRAPH.format(path=self.file_path))
        with open(self.file_path, encoding=cs.ENCODING_UTF8) as f:
            self._data = json.load(f)

        if self._data is None:
            raise RuntimeError(ex.FAILED_TO_LOAD_DATA)

        self._nodes = []
        for node_data in self._data[cs.KEY_NODES]:
            node = GraphNode(
                node_id=node_data[cs.KEY_NODE_ID],
                labels=node_data[cs.KEY_LABELS],
                properties=node_data[cs.KEY_PROPERTIES],
            )
            self._nodes.append(node)

            self._nodes_by_id[node.node_id] = node
            for label in node.labels:
                self._nodes_by_label[label].append(node)

        self._relationships = []
        for rel_data in self._data[cs.KEY_RELATIONSHIPS]:
            rel = GraphRelationship(
                from_id=rel_data[cs.KEY_FROM_ID],
                to_id=rel_data[cs.KEY_TO_ID],
                type=rel_data[cs.KEY_TYPE],
                properties=rel_data[cs.KEY_PROPERTIES],
            )
            self._relationships.append(rel)

            self._outgoing_rels[rel.from_id].append(rel)
            self._incoming_rels[rel.to_id].append(rel)

        logger.info(
            ls.LOADED_GRAPH.format(
                nodes=len(self._nodes), relationships=len(self._relationships)
            )
        )

    def _build_property_index(self, property_name: str) -> None:
        """Builds an index for a specific node property to speed up lookups.

        If the index for the given property name already exists, this method does nothing.

        Args:
            property_name (str): The name of the property to index.
        """
        if property_name in self._property_indexes:
            return

        index: defaultdict[PropertyValue, list[GraphNode]] = defaultdict(list)
        for node in self.nodes:
            value = node.properties.get(property_name)
            if value is not None:
                index[value].append(node)
        self._property_indexes[property_name] = dict(index)

    @property
    @ensure_loaded
    def nodes(self) -> list[GraphNode]:
        """Returns a list of all nodes in the graph."""
        assert self._nodes is not None, ex.NODES_NOT_LOADED
        return self._nodes

    @property
    @ensure_loaded
    def relationships(self) -> list[GraphRelationship]:
        """Returns a list of all relationships in the graph."""
        assert self._relationships is not None, ex.RELATIONSHIPS_NOT_LOADED
        return self._relationships

    @property
    @ensure_loaded
    def metadata(self) -> GraphMetadata:
        """Returns the metadata of the graph."""
        assert self._data is not None, ex.DATA_NOT_LOADED
        return self._data[cs.KEY_METADATA]

    @ensure_loaded
    def find_nodes_by_label(self, label: str) -> list[GraphNode]:
        """Finds all nodes with a specific label.

        Args:
            label (str): The label to search for.

        Returns:
            list[GraphNode]: A list of nodes that have the given label.
        """
        return self._nodes_by_label.get(label, [])

    @ensure_loaded
    def find_node_by_property(
        self, property_name: str, value: PropertyValue
    ) -> list[GraphNode]:
        """Finds nodes by a specific property and value.

        Args:
            property_name (str): The name of the property to search by.
            value (PropertyValue): The value of the property to match.

        Returns:
            list[GraphNode]: A list of nodes matching the property and value.
        """
        self._build_property_index(property_name)
        return self._property_indexes[property_name].get(value, [])

    @ensure_loaded
    def get_node_by_id(self, node_id: int) -> GraphNode | None:
        """Retrieves a single node by its unique ID.

        Args:
            node_id (int): The ID of the node to retrieve.

        Returns:
            GraphNode | None: The node if found, otherwise None.
        """
        return self._nodes_by_id.get(node_id)

    def get_relationships_for_node(self, node_id: int) -> list[GraphRelationship]:
        """Gets all incoming and outgoing relationships for a given node.

        Args:
            node_id (int): The ID of the node.

        Returns:
            list[GraphRelationship]: A list of all relationships connected to the node.
        """
        return self.get_outgoing_relationships(
            node_id
        ) + self.get_incoming_relationships(node_id)

    @ensure_loaded
    def get_outgoing_relationships(self, node_id: int) -> list[GraphRelationship]:
        """Gets all outgoing relationships from a specific node.

        Args:
            node_id (int): The ID of the source node.

        Returns:
            list[GraphRelationship]: A list of outgoing relationships.
        """
        return self._outgoing_rels.get(node_id, [])

    @ensure_loaded
    def get_incoming_relationships(self, node_id: int) -> list[GraphRelationship]:
        """Gets all incoming relationships to a specific node.

        Args:
            node_id (int): The ID of the target node.

        Returns:
            list[GraphRelationship]: A list of incoming relationships.
        """
        return self._incoming_rels.get(node_id, [])

    def summary(self) -> GraphSummary:
        """Generates a summary of the graph's contents.

        The summary includes total counts of nodes and relationships, as well as
        breakdowns by node label and relationship type.

        Returns:
            GraphSummary: An object containing the graph summary statistics.
        """
        node_labels = {
            label: len(nodes) for label, nodes in self._nodes_by_label.items()
        }
        relationship_types = dict(Counter(rel.type for rel in self.relationships))

        return GraphSummary(
            total_nodes=len(self.nodes),
            total_relationships=len(self.relationships),
            node_labels=node_labels,
            relationship_types=relationship_types,
            metadata=self.metadata,
        )


def load_graph(file_path: str) -> GraphLoader:
    """Creates a GraphLoader instance and loads the graph data.

    This is a convenience function to instantiate and load a graph in one step.

    Args:
        file_path (str): The path to the JSON graph file.

    Returns:
        GraphLoader: A loaded instance of the GraphLoader.
    """
    loader = GraphLoader(file_path)
    loader.load()
    return loader
