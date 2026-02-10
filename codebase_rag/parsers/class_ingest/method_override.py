"""
This module is responsible for processing method override relationships.

After all class and method definitions have been ingested into the graph, this
module iterates through all identified methods. For each method, it traverses
the class inheritance hierarchy to find if the method overrides a method from a
parent class.

If an overridden method is found, it creates an `OVERRIDES` relationship in the
graph between the child method and the parent method.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from loguru import logger

from codebase_rag.data_models.types_defs import NodeType

from ...core import constants as cs
from ...core import logs

if TYPE_CHECKING:
    from codebase_rag.data_models.types_defs import FunctionRegistryTrieProtocol

    from ...services import IngestorProtocol


def process_all_method_overrides(
    function_registry: FunctionRegistryTrieProtocol,
    class_inheritance: dict[str, list[str]],
    ingestor: IngestorProtocol,
) -> None:
    """
    Iterates through all methods and checks for overrides in parent classes.

    Args:
        function_registry (FunctionRegistryTrieProtocol): The registry of all functions and methods.
        class_inheritance (dict[str, list[str]]): A dictionary mapping class FQNs to their parents.
        ingestor (IngestorProtocol): The data ingestion service.
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
    Checks for and ingests an `OVERRIDES` relationship for a single method.

    It performs a breadth-first search up the inheritance hierarchy of the method's
    class to find a method with the same name.

    Args:
        method_qn (str): The FQN of the method to check.
        method_name (str): The simple name of the method.
        class_qn (str): The FQN of the class containing the method.
        function_registry (FunctionRegistryTrieProtocol): The registry of all functions.
        class_inheritance (dict): The class inheritance map.
        ingestor (IngestorProtocol): The data ingestion service.
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
