"""
This module provides the `ProtobufFileIngestor`, an alternative implementation of
the `IngestorProtocol`.

Instead of writing data to a live graph database like Memgraph, this ingestor
buffers nodes and relationships in memory and then serializes them to binary files
using Google Protocol Buffers (protobuf). This is useful for offline processing,
creating portable indexes of a codebase, or for scenarios where a live database
connection is not available or desired during the parsing phase.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

import codec.schema_pb2 as pb
from codebase_rag.data_models.types_defs import PropertyDict, PropertyValue

from ..core import constants as cs
from ..core import logs as ls

LABEL_TO_ONEOF_FIELD: dict[cs.NodeLabel, str] = {
    cs.NodeLabel.PROJECT: cs.ONEOF_PROJECT,
    cs.NodeLabel.PACKAGE: cs.ONEOF_PACKAGE,
    cs.NodeLabel.FOLDER: cs.ONEOF_FOLDER,
    cs.NodeLabel.MODULE: cs.ONEOF_MODULE,
    cs.NodeLabel.CLASS: cs.ONEOF_CLASS,
    cs.NodeLabel.FUNCTION: cs.ONEOF_FUNCTION,
    cs.NodeLabel.METHOD: cs.ONEOF_METHOD,
    cs.NodeLabel.FILE: cs.ONEOF_FILE,
    cs.NodeLabel.EXTERNAL_PACKAGE: cs.ONEOF_EXTERNAL_PACKAGE,
    cs.NodeLabel.MODULE_IMPLEMENTATION: cs.ONEOF_MODULE_IMPLEMENTATION,
    cs.NodeLabel.MODULE_INTERFACE: cs.ONEOF_MODULE_INTERFACE,
}
"""A mapping from internal `NodeLabel` enums to their corresponding 'oneof' field
names in the `Node` protobuf message schema."""

ONEOF_FIELD_TO_LABEL: dict[str, cs.NodeLabel] = {
    v: k for k, v in LABEL_TO_ONEOF_FIELD.items()
}
"""A reverse mapping from protobuf 'oneof' field names back to `NodeLabel` enums."""

PATH_BASED_LABELS = frozenset({cs.NodeLabel.FOLDER, cs.NodeLabel.FILE})
"""A set of node labels that use the 'path' property as their unique identifier."""
NAME_BASED_LABELS = frozenset({cs.NodeLabel.EXTERNAL_PACKAGE, cs.NodeLabel.PROJECT})
"""A set of node labels that use the 'name' property as their unique identifier."""


class ProtobufFileIngestor:
    """
    An ingestor that writes graph data to protobuf files instead of a live database.

    This class implements the `IngestorProtocol` but directs its output to binary
    files, which can be loaded into a database or used for other offline analysis later.
    """

    def __init__(self, output_path: str, split_index: bool = False):
        """
        Initializes the ProtobufFileIngestor.

        Args:
            output_path (str): The directory where the protobuf files will be saved.
            split_index (bool): If True, saves nodes and relationships into
                                separate files (`nodes.bin`, `relationships.bin`).
                                Otherwise, saves to a single `index.bin`.
        """
        self.output_dir = Path(output_path)
        self._nodes: dict[str, pb.Node] = {}
        self._relationships: dict[tuple[str, int, str], pb.Relationship] = {}
        self.split_index = split_index
        logger.info(ls.PROTOBUF_INIT.format(path=self.output_dir))

    def _get_node_id(self, label: cs.NodeLabel, properties: PropertyDict) -> str:
        """
        Determines the unique ID for a node based on its label and properties.

        This logic mirrors how unique constraints are typically handled in the graph
        database, using 'path' for files/folders, 'name' for projects, and
        'qualified_name' for most other code entities.

        Args:
            label (cs.NodeLabel): The label of the node.
            properties (PropertyDict): The properties of the node.

        Returns:
            The unique identifier for the node as a string.
        """
        if label in PATH_BASED_LABELS:
            return str(properties.get(cs.KEY_PATH, ""))
        if label in NAME_BASED_LABELS:
            return str(properties.get(cs.KEY_NAME, ""))
        return str(properties.get(cs.KEY_QUALIFIED_NAME, ""))

    def ensure_node_batch(self, label: str, properties: PropertyDict) -> None:
        """
        Adds a node to the in-memory buffer.

        This method converts the given properties into a protobuf `Node` message
        and stores it in an internal dictionary, ready to be flushed to a file.

        Args:
            label (str): The label of the node (e.g., "Function").
            properties (PropertyDict): A dictionary of properties for the node.
        """
        self._apply_project_context(label, properties)
        node_label = cs.NodeLabel(label)
        node_id = self._get_node_id(node_label, properties)
        if not node_id or node_id in self._nodes:
            return

        payload_message_class = getattr(pb, label, None)
        if not payload_message_class:
            logger.warning(ls.PROTOBUF_NO_MESSAGE_CLASS.format(label=label))
            return

        payload_message = payload_message_class()

        for key, value in properties.items():
            if hasattr(payload_message, key):
                if value is None:
                    continue
                destination_attribute = getattr(payload_message, key)
                if hasattr(destination_attribute, "extend") and isinstance(value, list):
                    destination_attribute.extend(value)
                else:
                    setattr(payload_message, key, value)

        node = pb.Node()

        payload_field_name = LABEL_TO_ONEOF_FIELD.get(node_label)
        if not payload_field_name:
            logger.warning(ls.PROTOBUF_NO_ONEOF_MAPPING.format(label=label))
            return

        getattr(node, payload_field_name).CopyFrom(payload_message)

        self._nodes[node_id] = node

    def ensure_relationship_batch(
        self,
        from_spec: tuple[str, str, PropertyValue],
        rel_type: str,
        to_spec: tuple[str, str, PropertyValue],
        properties: PropertyDict | None = None,
    ) -> None:
        """
        Adds a relationship to the in-memory buffer.

        Args:
            from_spec (tuple): A tuple identifying the source node (label, key, value).
            rel_type (str): The type of the relationship (e.g., "CALLS").
            to_spec (tuple): A tuple identifying the target node (label, key, value).
            properties (PropertyDict | None): Optional properties for the relationship.
        """
        rel = pb.Relationship()

        rel_type_enum = getattr(pb.Relationship.RelationshipType, rel_type, None)
        if rel_type_enum is None:
            logger.warning(ls.PROTOBUF_UNKNOWN_REL_TYPE.format(rel_type=rel_type))
            rel_type_enum = (
                pb.Relationship.RelationshipType.RELATIONSHIP_TYPE_UNSPECIFIED
            )
        rel.type = rel_type_enum

        from_label, _, from_val = from_spec
        to_label, _, to_val = to_spec

        rel.source_id = str(from_val)
        rel.source_label = str(from_label)
        rel.target_id = str(to_val)
        rel.target_label = str(to_label)

        if not rel.source_id.strip() or not rel.target_id.strip():
            logger.warning(
                ls.PROTOBUF_INVALID_REL.format(
                    source_id=rel.source_id, target_id=rel.target_id
                )
            )
            return

        rel_props: PropertyDict = dict(properties) if properties else {}
        project_name = getattr(self, "project_name", None)
        if project_name and cs.KEY_PROJECT_NAME not in rel_props:
            rel_props[cs.KEY_PROJECT_NAME] = project_name
        if from_spec[1] == cs.KEY_PATH and cs.KEY_FROM_PATH not in rel_props:
            rel_props[cs.KEY_FROM_PATH] = from_spec[2]
        if to_spec[1] == cs.KEY_PATH and cs.KEY_TO_PATH not in rel_props:
            rel_props[cs.KEY_TO_PATH] = to_spec[2]
        if rel_props:
            rel.properties.update(rel_props)

        unique_key = (rel.source_id, rel.type, rel.target_id)
        if unique_key in self._relationships:
            if properties:
                existing_rel = self._relationships[unique_key]
                existing_rel.properties.update(properties)
        else:
            self._relationships[unique_key] = rel

    def _flush_joint(self) -> None:
        """Flushes both nodes and relationships to a single `index.bin` file."""
        index = pb.GraphCodeIndex()
        index.nodes.extend(self._nodes.values())
        index.relationships.extend(self._relationships.values())

        serialised_file = index.SerializeToString()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        out_path = self.output_dir / cs.PROTOBUF_INDEX_FILE
        with open(out_path, "wb") as f:
            f.write(serialised_file)

        logger.success(
            ls.PROTOBUF_FLUSH_SUCCESS.format(
                nodes=len(self._nodes),
                rels=len(self._relationships),
                path=self.output_dir,
            )
        )

    def _flush_split(self) -> None:
        """Flushes nodes and relationships to separate `nodes.bin` and `relationships.bin` files."""
        nodes_index = pb.GraphCodeIndex()
        rels_index = pb.GraphCodeIndex()
        nodes_index.nodes.extend(self._nodes.values())
        rels_index.relationships.extend(self._relationships.values())

        serialised_nodes = nodes_index.SerializeToString()
        serialised_rels = rels_index.SerializeToString()

        self.output_dir.mkdir(parents=True, exist_ok=True)
        nodes_path = self.output_dir / cs.PROTOBUF_NODES_FILE
        rels_path = self.output_dir / cs.PROTOBUF_RELS_FILE

        with open(nodes_path, "wb") as f:
            f.write(serialised_nodes)

        with open(rels_path, "wb") as f:
            f.write(serialised_rels)

        logger.success(
            ls.PROTOBUF_FLUSH_SUCCESS.format(
                nodes=len(self._nodes),
                rels=len(self._relationships),
                path=self.output_dir,
            )
        )

    def flush_all(self) -> None:
        """
        Flushes all buffered nodes and relationships to protobuf files.

        This method decides whether to create a single combined file or separate
        files for nodes and relationships based on the `split_index` attribute.
        """
        logger.info(ls.PROTOBUF_FLUSHING.format(path=self.output_dir))

        return self._flush_split() if self.split_index else self._flush_joint()

    def _apply_project_context(self, label: str, properties: PropertyDict) -> None:
        """
        Applies project-level context (like project_name) to node properties before ingestion.

        Args:
            label (str): The label of the node.
            properties (PropertyDict): The properties of the node.
        """
        project_name = getattr(self, "project_name", None)
        if project_name and cs.KEY_PROJECT_NAME not in properties:
            properties[cs.KEY_PROJECT_NAME] = project_name

        path_value = properties.get(cs.KEY_PATH)
        if not path_value:
            return

        folder_path, folder_name = self._derive_folder_props(
            label, str(path_value), project_name
        )
        if folder_path and cs.KEY_FOLDER_PATH not in properties:
            properties[cs.KEY_FOLDER_PATH] = folder_path
        if folder_name and cs.KEY_FOLDER_NAME not in properties:
            properties[cs.KEY_FOLDER_NAME] = folder_name

    @staticmethod
    def _derive_folder_props(
        label: str, path_value: str, project_name: str | None
    ) -> tuple[str, str]:
        """
        Derives folder path and name from a node's path property.

        Args:
            label (str): The node's label.
            path_value (str): The node's path property.
            project_name (str | None): The name of the project.

        Returns:
            A tuple of (folder_path, folder_name).
        """
        try:
            node_label = cs.NodeLabel(label)
        except Exception:
            node_label = None

        path_obj = Path(path_value)
        if node_label in {cs.NodeLabel.FOLDER, cs.NodeLabel.PACKAGE}:
            folder_path = path_obj.as_posix()
        else:
            folder_path = path_obj.parent.as_posix()

        if folder_path in {"", "."}:
            return ".", project_name or path_obj.name

        return folder_path, Path(folder_path).name
