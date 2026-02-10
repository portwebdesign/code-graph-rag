"""
This module defines the `JavaTypeResolverMixin`, a component responsible for
resolving Java type names to their fully qualified names (FQNs).

Java's type system, with its packages, imports (including wildcards), and nested
classes, requires specific logic for accurate resolution. This mixin encapsulates
that logic.

Key functionalities:
-   Resolving a simple type name (e.g., `String`, `MyClass`) to its FQN
    (e.g., `java.lang.String`, `com.mycompany.MyClass`).
-   Handling primitive types, wrapper types, and arrays.
-   Using the current module's import map to resolve types.
-   Searching within the same package for unresolved types.
-   Traversing the AST to find superclass and interface FQNs for a given class.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from codebase_rag.data_models.types_defs import ASTNode, NodeType

from ...core import constants as cs
from .utils import (
    find_package_start_index,
    get_class_context_from_qn,
    get_root_node_from_module_qn,
    safe_decode_text,
)

if TYPE_CHECKING:
    from pathlib import Path

    from codebase_rag.data_models.types_defs import (
        ASTCacheProtocol,
        FunctionRegistryTrieProtocol,
    )

    from ..import_processor import ImportProcessor


class JavaTypeResolverMixin:
    """
    A mixin providing methods to resolve Java type names to their FQNs.
    """

    import_processor: ImportProcessor
    function_registry: FunctionRegistryTrieProtocol
    module_qn_to_file_path: dict[str, Path]
    ast_cache: ASTCacheProtocol
    _fqn_to_module_qn: dict[str, list[str]]

    def _module_qn_to_java_fqn(self, module_qn: str) -> str | None:
        """Converts an internal module FQN to a Java-style FQN."""
        parts = module_qn.split(cs.SEPARATOR_DOT)
        package_start_idx = find_package_start_index(parts)
        if package_start_idx is None:
            return None
        class_parts = parts[package_start_idx:]
        return cs.SEPARATOR_DOT.join(class_parts) if class_parts else None

    def _calculate_module_distance(
        self, candidate_qn: str, caller_module_qn: str
    ) -> int:
        """Calculates a 'distance' score between two module FQNs."""
        caller_parts = caller_module_qn.split(cs.SEPARATOR_DOT)
        candidate_parts = candidate_qn.split(cs.SEPARATOR_DOT)

        common_prefix = 0
        for caller_part, candidate_part in zip(caller_parts, candidate_parts):
            if caller_part == candidate_part:
                common_prefix += 1
            else:
                break

        base_distance = max(len(caller_parts), len(candidate_parts)) - common_prefix

        if (
            len(caller_parts) > 1
            and candidate_parts[: len(caller_parts) - 1] == caller_parts[:-1]
        ):
            base_distance -= 1

        return max(base_distance, 0)

    def _rank_module_candidates(
        self,
        candidates: list[str],
        class_qn: str,
        current_module_qn: str | None,
    ) -> list[str]:
        """Ranks potential module candidates based on their relevance to the current context."""
        if not candidates or not current_module_qn:
            return candidates

        ranked: list[tuple[tuple[int, int, int], str]] = []
        for idx, candidate in enumerate(candidates):
            candidate_fqn = self._module_qn_to_java_fqn(candidate)

            if candidate_fqn == class_qn:
                match_penalty = 0
            elif candidate_fqn and class_qn.endswith(candidate_fqn):
                match_penalty = 1
            else:
                match_penalty = 2

            distance = self._calculate_module_distance(candidate, current_module_qn)
            ranked.append(((match_penalty, distance, idx), candidate))

        ranked.sort(key=lambda item: item[0])
        return [candidate for _, candidate in ranked]

    def _find_registry_entries_under(self, prefix: str) -> Iterable[tuple[str, str]]:
        """Finds all entries in the function registry that start with a given prefix."""
        finder = getattr(self.function_registry, cs.METHOD_FIND_WITH_PREFIX, None)
        if callable(finder):
            if matches := list(finder(prefix)):
                return matches

        items = getattr(self.function_registry, cs.METHOD_ITEMS, None)
        if callable(items):
            prefix_with_dot = f"{prefix}{cs.SEPARATOR_DOT}"
            return [
                (qn, method_type)
                for qn, method_type in items()
                if qn.startswith(prefix_with_dot) or qn == prefix
            ]

        return []

    def _resolve_java_type_name(self, type_name: str, module_qn: str) -> str:
        """
        Resolves a Java type name to its fully qualified name.

        Args:
            type_name (str): The simple or partially qualified type name.
            module_qn (str): The FQN of the module where the type is used.

        Returns:
            str: The resolved fully qualified name.
        """
        if not type_name:
            return cs.JAVA_TYPE_OBJECT

        if cs.SEPARATOR_DOT in type_name:
            return type_name

        if type_name in cs.JAVA_PRIMITIVE_TYPES:
            return type_name

        if type_name in cs.JAVA_WRAPPER_TYPES:
            return f"{cs.JAVA_LANG_PREFIX}{type_name}"

        if type_name.endswith(cs.JAVA_ARRAY_SUFFIX):
            base_type = type_name[:-2]
            resolved_base = self._resolve_java_type_name(base_type, module_qn)
            return f"{resolved_base}{cs.JAVA_ARRAY_SUFFIX}"

        if cs.CHAR_ANGLE_OPEN in type_name and cs.CHAR_ANGLE_CLOSE in type_name:
            base_type = type_name.split(cs.CHAR_ANGLE_OPEN)[0]
            return self._resolve_java_type_name(base_type, module_qn)

        if module_qn in self.import_processor.import_mapping:
            import_map = self.import_processor.import_mapping[module_qn]
            if type_name in import_map:
                return import_map[type_name]

        same_package_qn = f"{module_qn}{cs.SEPARATOR_DOT}{type_name}"
        if same_package_qn in self.function_registry and self.function_registry[
            same_package_qn
        ] in [NodeType.CLASS, NodeType.INTERFACE]:
            return same_package_qn

        return type_name

    def _get_superclass_name(self, class_qn: str) -> str | None:
        """
        Retrieves the fully qualified name of the superclass for a given class.

        Args:
            class_qn (str): The FQN of the class to check.

        Returns:
            str | None: The FQN of the superclass, or None if not found.
        """
        ctx = get_class_context_from_qn(
            class_qn, self.module_qn_to_file_path, self.ast_cache
        )
        if not ctx:
            return None

        return self._find_superclass_using_ast(
            ctx.root_node, ctx.target_class_name, ctx.module_qn
        )

    def _find_superclass_using_ast(
        self, node: ASTNode, target_class_name: str, module_qn: str
    ) -> str | None:
        """Recursively searches the AST for a class and extracts its superclass."""
        if node.type == cs.TS_CLASS_DECLARATION:
            if (
                name_node := node.child_by_field_name(cs.FIELD_NAME)
            ) and safe_decode_text(name_node) == target_class_name:
                if superclass_node := node.child_by_field_name(cs.FIELD_SUPERCLASS):
                    if superclass_name := self._extract_type_name_from_node(
                        superclass_node
                    ):
                        return self._resolve_java_type_name(superclass_name, module_qn)

        for child in node.children:
            if result := self._find_superclass_using_ast(
                child, target_class_name, module_qn
            ):
                return result

        return None

    def _extract_type_name_from_node(self, parent_node: ASTNode) -> str | None:
        """Extracts a type name from a `generic_type` or `type_identifier` node."""
        for child in parent_node.children:
            if child.type == cs.TS_GENERIC_TYPE:
                for subchild in child.children:
                    if subchild.type == cs.TS_TYPE_IDENTIFIER:
                        return safe_decode_text(subchild)
            elif child.type == cs.TS_TYPE_IDENTIFIER:
                return safe_decode_text(child)
        return None

    def _get_implemented_interfaces(self, class_qn: str) -> list[str]:
        """
        Retrieves the FQNs of all interfaces implemented by a given class.

        Args:
            class_qn (str): The FQN of the class.

        Returns:
            list[str]: A list of FQNs of the implemented interfaces.
        """
        parts = class_qn.split(cs.SEPARATOR_DOT)
        if len(parts) < 2:
            return []

        module_qn = cs.SEPARATOR_DOT.join(parts[:-1])
        target_class_name = parts[-1]

        file_path = self.module_qn_to_file_path.get(module_qn)
        if file_path is None or file_path not in self.ast_cache:
            return []

        root_node, _ = self.ast_cache[file_path]

        return self._find_interfaces_using_ast(root_node, target_class_name, module_qn)

    def _find_interfaces_using_ast(
        self, node: ASTNode, target_class_name: str, module_qn: str
    ) -> list[str]:
        """Recursively searches the AST for a class and extracts its implemented interfaces."""
        if node.type == cs.TS_CLASS_DECLARATION:
            if (
                name_node := node.child_by_field_name(cs.FIELD_NAME)
            ) and safe_decode_text(name_node) == target_class_name:
                if interfaces_node := node.child_by_field_name(cs.FIELD_INTERFACES):
                    interface_list: list[str] = []
                    self._extract_interface_names(
                        interfaces_node, interface_list, module_qn
                    )
                    return interface_list

        for child in node.children:
            if result := self._find_interfaces_using_ast(
                child, target_class_name, module_qn
            ):
                return result

        return []

    def _extract_interface_names(
        self, interfaces_node: ASTNode, interface_list: list[str], module_qn: str
    ) -> None:
        """Extracts interface names from an `implements` clause node."""
        for child in interfaces_node.children:
            if child.type == cs.TS_TYPE_IDENTIFIER:
                if interface_name := safe_decode_text(child):
                    resolved_interface = self._resolve_java_type_name(
                        interface_name, module_qn
                    )
                    interface_list.append(resolved_interface)
            elif child.children:
                self._extract_interface_names(child, interface_list, module_qn)

    def _get_current_class_name(self, module_qn: str) -> str | None:
        """
        Gets the FQN of the primary class defined in a module.

        Args:
            module_qn (str): The FQN of the module.

        Returns:
            str | None: The FQN of the primary class, or None.
        """
        root_node = get_root_node_from_module_qn(
            module_qn, self.module_qn_to_file_path, self.ast_cache
        )
        if not root_node:
            return None

        class_names: list[str] = []
        self._traverse_for_class_declarations(root_node, class_names)

        return f"{module_qn}{cs.SEPARATOR_DOT}{class_names[0]}" if class_names else None

    def _traverse_for_class_declarations(
        self, node: ASTNode, class_names: list[str]
    ) -> None:
        """Recursively traverses the AST to find all class declaration names."""
        match node.type:
            case (
                cs.TS_CLASS_DECLARATION
                | cs.TS_INTERFACE_DECLARATION
                | cs.TS_ENUM_DECLARATION
            ):
                if (name_node := node.child_by_field_name(cs.FIELD_NAME)) and (
                    class_name := safe_decode_text(name_node)
                ):
                    class_names.append(class_name)
            case _:
                pass

        for child in node.children:
            self._traverse_for_class_declarations(child, class_names)
