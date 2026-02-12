from __future__ import annotations

from codebase_rag.data_models.types_defs import TreeSitterNodeProtocol


class NoopTypeInferenceEngine:
    """
    Type inference engine for data-centric languages or when inference is disabled.

    This class adheres to the type inference protocol but returns empty results,
    effectively performing no operation.
    """

    def build_local_variable_type_map(
        self, caller_node: TreeSitterNodeProtocol, module_qn: str
    ) -> dict[str, str]:
        """
        Returns an empty type map.

        Args:
            caller_node (TreeSitterNodeProtocol): The node where the call is made.
            module_qn (str): The qualified name of the module.

        Returns:
            dict[str, str]: An empty dictionary.
        """
        return {}
