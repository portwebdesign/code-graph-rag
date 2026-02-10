"""
This module contains functions for creating relationships associated with
class-like nodes in the knowledge graph.

It handles the creation of:
-   `DEFINES` relationships from a module to the class it contains.
-   `EXPORTS` relationships for explicitly exported classes (e.g., in C++).
-   `INHERITS` relationships between a class and its parent classes.
-   `IMPLEMENTS` relationships between a class and the interfaces it implements.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from tree_sitter import Node

from codebase_rag.data_models.types_defs import NodeType

from ...core import constants as cs
from . import parent_extraction as pe

if TYPE_CHECKING:
    from codebase_rag.data_models.types_defs import FunctionRegistryTrieProtocol

    from ...services import IngestorProtocol
    from ..import_processor import ImportProcessor


def create_class_relationships(
    class_node: Node,
    class_qn: str,
    module_qn: str,
    node_type: NodeType,
    is_exported: bool,
    language: cs.SupportedLanguage,
    class_inheritance: dict[str, list[str]],
    ingestor: IngestorProtocol,
    import_processor: ImportProcessor,
    resolve_to_qn: Callable[[str, str], str],
    function_registry: FunctionRegistryTrieProtocol,
) -> None:
    """
    Creates all relevant relationships for a class node.

    Args:
        class_node (Node): The AST node of the class.
        class_qn (str): The fully qualified name of the class.
        module_qn (str): The FQN of the containing module.
        node_type (NodeType): The specific type of the class-like node.
        is_exported (bool): Whether the class is exported from its module.
        language (cs.SupportedLanguage): The language of the code.
        class_inheritance (dict): A dictionary to store inheritance relationships.
        ingestor (IngestorProtocol): The data ingestion service.
        import_processor (ImportProcessor): The processor for handling imports.
        resolve_to_qn (Callable): A function to resolve a simple name to an FQN.
        function_registry (FunctionRegistryTrieProtocol): The registry of all known functions.
    """
    parent_classes = pe.extract_parent_classes(
        class_node, module_qn, import_processor, resolve_to_qn
    )
    class_inheritance[class_qn] = parent_classes

    ingestor.ensure_relationship_batch(
        (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
        cs.RelationshipType.DEFINES,
        (node_type, cs.KEY_QUALIFIED_NAME, class_qn),
    )

    if is_exported and language == cs.SupportedLanguage.CPP:
        ingestor.ensure_relationship_batch(
            (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
            cs.RelationshipType.EXPORTS,
            (node_type, cs.KEY_QUALIFIED_NAME, class_qn),
        )

    for parent_class_qn in parent_classes:
        create_inheritance_relationship(
            node_type, class_qn, parent_class_qn, function_registry, ingestor
        )

    if class_node.type == cs.TS_CLASS_DECLARATION:
        for interface_qn in pe.extract_implemented_interfaces(
            class_node, module_qn, resolve_to_qn
        ):
            create_implements_relationship(node_type, class_qn, interface_qn, ingestor)


def get_node_type_for_inheritance(
    qualified_name: str,
    function_registry: FunctionRegistryTrieProtocol,
) -> str:
    """
    Determines the node type of a parent entity in an inheritance relationship.

    It defaults to 'Class' if the type is not explicitly found in the registry.

    Args:
        qualified_name (str): The FQN of the parent entity.
        function_registry (FunctionRegistryTrieProtocol): The function registry.

    Returns:
        str: The node label of the parent entity (e.g., 'Class', 'Interface').
    """
    node_type = function_registry.get(qualified_name, NodeType.CLASS)
    return str(node_type)


def create_inheritance_relationship(
    child_node_type: str,
    child_qn: str,
    parent_qn: str,
    function_registry: FunctionRegistryTrieProtocol,
    ingestor: IngestorProtocol,
) -> None:
    """
    Creates an `INHERITS` relationship in the graph.

    Args:
        child_node_type (str): The node label of the child class.
        child_qn (str): The FQN of the child class.
        parent_qn (str): The FQN of the parent class/interface.
        function_registry (FunctionRegistryTrieProtocol): The function registry.
        ingestor (IngestorProtocol): The data ingestion service.
    """
    parent_type = get_node_type_for_inheritance(parent_qn, function_registry)
    ingestor.ensure_relationship_batch(
        (child_node_type, cs.KEY_QUALIFIED_NAME, child_qn),
        cs.RelationshipType.INHERITS,
        (parent_type, cs.KEY_QUALIFIED_NAME, parent_qn),
    )


def create_implements_relationship(
    class_type: str,
    class_qn: str,
    interface_qn: str,
    ingestor: IngestorProtocol,
) -> None:
    """
    Creates an `IMPLEMENTS` relationship in the graph.

    Args:
        class_type (str): The node label of the implementing class.
        class_qn (str): The FQN of the implementing class.
        interface_qn (str): The FQN of the implemented interface.
        ingestor (IngestorProtocol): The data ingestion service.
    """
    ingestor.ensure_relationship_batch(
        (class_type, cs.KEY_QUALIFIED_NAME, class_qn),
        cs.RelationshipType.IMPLEMENTS,
        (cs.NodeLabel.INTERFACE, cs.KEY_QUALIFIED_NAME, interface_qn),
    )
