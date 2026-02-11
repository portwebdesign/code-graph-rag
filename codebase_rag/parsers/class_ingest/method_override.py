from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from loguru import logger

from codebase_rag.core import constants as cs
from codebase_rag.data_models.types_defs import NodeType

from ... import logs

if TYPE_CHECKING:
    from codebase_rag.data_models.types_defs import FunctionRegistryTrieProtocol
    from codebase_rag.services import IngestorProtocol


def process_all_method_overrides(
    function_registry: FunctionRegistryTrieProtocol,
    class_inheritance: dict[str, list[str]],
    ingestor: IngestorProtocol,
) -> None:
    """
    Process all method overrides for the entire codebase.

    Iterates through all registered methods and checks if they override a method
    in a parent class.

    Args:
        function_registry: The registry of known function/method nodes.
        class_inheritance: A mapping of class qualified names to their parent class qualified names.
        ingestor: The ingestor instance to use for creating OVERRIDES relationships.
    """
    logger.info(logs.CLASS_PASS_4)

    for method_qn in function_registry.keys():
        if (
            function_registry[method_qn] == NodeType.METHOD
            and cs.SEPARATOR_DOT in method_qn
        ):
            parts = method_qn.rsplit(cs.SEPARATOR_DOT, 1)
            if len(parts) == 2:
                class_qn, method_name = parts
                check_method_overrides(
                    method_qn,
                    method_name,
                    class_qn,
                    function_registry,
                    class_inheritance,
                    ingestor,
                )


def check_method_overrides(
    method_qn: str,
    method_name: str,
    class_qn: str,
    function_registry: FunctionRegistryTrieProtocol,
    class_inheritance: dict[str, list[str]],
    ingestor: IngestorProtocol,
) -> None:
    """
    Check if a specific method overrides a method in its parent hierarchy.

    Traverses the inheritance chain using BFS to find the nearest overridden method.

    Args:
        method_qn: The qualified name of the method to check.
        method_name: The simple name of the method.
        class_qn: The qualified name of the class containing the method.
        function_registry: The function registry.
        class_inheritance: The inheritance map.
        ingestor: The ingestor used to create the relationship.
    """
    if class_qn not in class_inheritance:
        return

    queue = deque([class_qn])
    visited = {class_qn}

    while queue:
        current_class = queue.popleft()

        if current_class != class_qn:
            parent_method_qn = f"{current_class}.{method_name}"

            if parent_method_qn in function_registry:
                ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.METHOD, cs.KEY_QUALIFIED_NAME, method_qn),
                    cs.RelationshipType.OVERRIDES,
                    (cs.NodeLabel.METHOD, cs.KEY_QUALIFIED_NAME, parent_method_qn),
                )
                logger.debug(
                    logs.CLASS_METHOD_OVERRIDE.format(
                        method_qn=method_qn, parent_method_qn=parent_method_qn
                    )
                )
                return

        if current_class in class_inheritance:
            for parent_class_qn in class_inheritance[current_class]:
                if parent_class_qn not in visited:
                    visited.add(parent_class_qn)
                    queue.append(parent_class_qn)
