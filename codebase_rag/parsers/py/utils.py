"""
This module provides utility functions specifically for parsing Python source code.

It contains helpers for resolving class names, which is a common task across
different Python-related parsers.
"""

from typing import TYPE_CHECKING

from codebase_rag.core.constants import SEPARATOR_DOT
from codebase_rag.data_models.types_defs import FunctionRegistryTrieProtocol

if TYPE_CHECKING:
    from ..import_processor import ImportProcessor


def resolve_class_name(
    class_name: str,
    module_qn: str,
    import_processor: "ImportProcessor",
    function_registry: FunctionRegistryTrieProtocol,
) -> str | None:
    """
    Resolves a simple class name to its fully qualified name (FQN).

    It uses a multi-step process:
    1. Check the import map of the current module.
    2. Check for a class with the same name in the current module.
    3. Traverse up the module hierarchy to check parent modules.
    4. As a fallback, search the entire function registry for any FQN ending
       with the class name.

    Args:
        class_name (str): The simple name of the class to resolve.
        module_qn (str): The FQN of the module where the class name is used.
        import_processor (ImportProcessor): The import processor instance.
        function_registry (FunctionRegistryTrieProtocol): The registry of all known functions/classes.

    Returns:
        str | None: The resolved FQN of the class, or None if it cannot be found.
    """
    if module_qn in import_processor.import_mapping:
        import_map = import_processor.import_mapping[module_qn]
        if class_name in import_map:
            return import_map[class_name]

    same_module_qn = f"{module_qn}.{class_name}"
    if same_module_qn in function_registry:
        return same_module_qn

    module_parts = module_qn.split(SEPARATOR_DOT)
    for i in range(len(module_parts) - 1, 0, -1):
        parent_module = SEPARATOR_DOT.join(module_parts[:i])
        potential_qn = f"{parent_module}.{class_name}"
        if potential_qn in function_registry:
            return potential_qn

    matches = function_registry.find_ending_with(class_name)
    for match in matches:
        match_parts = match.split(SEPARATOR_DOT)
        if class_name in match_parts:
            return str(match)

    return None
