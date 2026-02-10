"""
This module defines the `CallResolver`, a class dedicated to resolving function
and method calls found in the source code.

It uses various strategies to determine the fully qualified name (FQN) of a
callee, including:
-   Checking direct imports within the current module.
-   Using type inference information for variables and class instances.
-   Resolving methods on objects, including inherited methods.
-   Handling special cases like `super()` calls and chained method calls.
-   Falling back to a trie-based search of the entire function registry.

The resolver is a key component of the `CallProcessor` and relies on data
structures like the function registry, import maps, and type inference results
to perform its work.
"""

from __future__ import annotations

import re

from loguru import logger
from tree_sitter import Node

from codebase_rag.data_models.types_defs import FunctionRegistryTrieProtocol, NodeType

from ..core import constants as cs
from ..core import logs as ls
from .import_processor import ImportProcessor
from .py import resolve_class_name
from .type_inference import TypeInferenceEngine


class CallResolver:
    """
    Resolves function and method calls to their fully qualified names.
    """

    def __init__(
        self,
        function_registry: FunctionRegistryTrieProtocol,
        import_processor: ImportProcessor,
        type_inference: TypeInferenceEngine,
        class_inheritance: dict[str, list[str]],
    ) -> None:
        """
        Initializes the CallResolver.

        Args:
            function_registry (FunctionRegistryTrieProtocol): The registry of all known functions.
            import_processor (ImportProcessor): The processor for handling imports.
            type_inference (TypeInferenceEngine): The engine for inferring variable types.
            class_inheritance (dict[str, list[str]]): A dictionary mapping classes to their parents.
        """
        self.function_registry = function_registry
        self.import_processor = import_processor
        self.type_inference = type_inference
        self.class_inheritance = class_inheritance

    def _resolve_class_qn_from_type(
        self, var_type: str, import_map: dict[str, str], module_qn: str
    ) -> str:
        """
        Resolves a simple type name to a fully qualified class name.

        Args:
            var_type (str): The simple type name (e.g., 'MyClass').
            import_map (dict[str, str]): The import map for the current module.
            module_qn (str): The qualified name of the current module.

        Returns:
            str: The resolved fully qualified name of the class, or an empty string.
        """
        if cs.SEPARATOR_DOT in var_type:
            return var_type
        if var_type in import_map:
            return import_map[var_type]
        return self._resolve_class_name(var_type, module_qn) or ""

    def _try_resolve_method(
        self, class_qn: str, method_name: str, separator: str = cs.SEPARATOR_DOT
    ) -> tuple[str, str] | None:
        """
        Tries to resolve a method on a class, including inherited methods.

        Args:
            class_qn (str): The fully qualified name of the class.
            method_name (str): The name of the method.
            separator (str): The separator used in the method call.

        Returns:
            tuple[str, str] | None: A tuple of (node_type, fqn) if resolved, else None.
        """
        method_qn = f"{class_qn}{separator}{method_name}"
        if method_qn in self.function_registry:
            return self.function_registry[method_qn], method_qn
        return self._resolve_inherited_method(class_qn, method_name)

    def resolve_function_call(
        self,
        call_name: str,
        module_qn: str,
        local_var_types: dict[str, str] | None = None,
        class_context: str | None = None,
    ) -> tuple[str, str] | None:
        """
        Main entry point for resolving a function or method call.

        Args:
            call_name (str): The name of the function/method as it appears in the code.
            module_qn (str): The qualified name of the module where the call occurs.
            local_var_types (dict | None): A map of local variables to their inferred types.
            class_context (str | None): The FQN of the class if the call is within a method.

        Returns:
            tuple[str, str] | None: A tuple of (node_type, fqn) if resolved, else None.
        """
        if result := self._try_resolve_iife(call_name, module_qn):
            return result

        if self._is_super_call(call_name):
            return self._resolve_super_call(call_name, class_context)

        if cs.SEPARATOR_DOT in call_name and self._is_method_chain(call_name):
            return self._resolve_chained_call(call_name, module_qn, local_var_types)

        if result := self._try_resolve_via_imports(
            call_name, module_qn, local_var_types
        ):
            return result

        if result := self._try_resolve_same_module(call_name, module_qn):
            return result

        return self._try_resolve_via_trie(call_name, module_qn)

    def _try_resolve_iife(
        self, call_name: str, module_qn: str
    ) -> tuple[str, str] | None:
        """
        Tries to resolve an Immediately Invoked Function Expression (IIFE).

        Args:
            call_name (str): The generated name of the IIFE.
            module_qn (str): The qualified name of the module.

        Returns:
            tuple[str, str] | None: A tuple of (node_type, fqn) if resolved, else None.
        """
        if not call_name:
            return None
        if not (
            call_name.startswith(cs.IIFE_FUNC_PREFIX)
            or call_name.startswith(cs.IIFE_ARROW_PREFIX)
        ):
            return None
        iife_qn = f"{module_qn}.{call_name}"
        if iife_qn in self.function_registry:
            return self.function_registry[iife_qn], iife_qn
        return None

    def _is_super_call(self, call_name: str) -> bool:
        """Checks if a call name refers to `super()`."""
        return (
            call_name == cs.KEYWORD_SUPER
            or call_name.startswith(f"{cs.KEYWORD_SUPER}.")
            or call_name.startswith(f"{cs.KEYWORD_SUPER}()")
        )

    def _try_resolve_via_imports(
        self,
        call_name: str,
        module_qn: str,
        local_var_types: dict[str, str] | None,
    ) -> tuple[str, str] | None:
        """
        Tries to resolve a call using the import map of the current module.

        Args:
            call_name (str): The name of the call.
            module_qn (str): The qualified name of the module.
            local_var_types (dict | None): A map of local variables to their types.

        Returns:
            tuple[str, str] | None: A tuple of (node_type, fqn) if resolved, else None.
        """
        if module_qn not in self.import_processor.import_mapping:
            return None

        import_map = self.import_processor.import_mapping[module_qn]

        if result := self._try_resolve_direct_import(call_name, import_map):
            return result

        if result := self._try_resolve_qualified_call(
            call_name, import_map, module_qn, local_var_types
        ):
            return result

        return self._try_resolve_wildcard_imports(call_name, import_map)

    def _try_resolve_direct_import(
        self, call_name: str, import_map: dict[str, str]
    ) -> tuple[str, str] | None:
        """
        Tries to resolve a call that matches a direct import.

        Args:
            call_name (str): The name of the call.
            import_map (dict[str, str]): The import map for the module.

        Returns:
            tuple[str, str] | None: A tuple of (node_type, fqn) if resolved, else None.
        """
        if call_name not in import_map:
            return None
        imported_qn = import_map[call_name]
        if imported_qn in self.function_registry:
            logger.debug(
                ls.CALL_DIRECT_IMPORT.format(call_name=call_name, qn=imported_qn)
            )
            return self.function_registry[imported_qn], imported_qn
        return None

    def _try_resolve_qualified_call(
        self,
        call_name: str,
        import_map: dict[str, str],
        module_qn: str,
        local_var_types: dict[str, str] | None,
    ) -> tuple[str, str] | None:
        """
        Tries to resolve a qualified call (e.g., `module.function()`).

        Args:
            call_name (str): The qualified call name.
            import_map (dict[str, str]): The import map for the module.
            module_qn (str): The qualified name of the module.
            local_var_types (dict | None): A map of local variables to their types.

        Returns:
            tuple[str, str] | None: A tuple of (node_type, fqn) if resolved, else None.
        """
        if not self._has_separator(call_name):
            return None

        separator = self._get_separator(call_name)
        parts = call_name.split(separator)

        if len(parts) == 2:
            if result := self._resolve_two_part_call(
                parts, call_name, separator, import_map, module_qn, local_var_types
            ):
                return result

        if len(parts) >= 3 and parts[0] == cs.KEYWORD_SELF:
            return self._resolve_self_attribute_call(
                parts, call_name, import_map, module_qn, local_var_types
            )

        return self._resolve_multi_part_call(
            parts, call_name, import_map, module_qn, local_var_types
        )

    def _has_separator(self, call_name: str) -> bool:
        """Checks if the call name contains a namespace separator."""
        return (
            cs.SEPARATOR_DOT in call_name
            or cs.SEPARATOR_DOUBLE_COLON in call_name
            or cs.SEPARATOR_COLON in call_name
        )

    def _get_separator(self, call_name: str) -> str:
        """Gets the namespace separator used in the call name."""
        if cs.SEPARATOR_DOUBLE_COLON in call_name:
            return cs.SEPARATOR_DOUBLE_COLON
        if cs.SEPARATOR_COLON in call_name:
            return cs.SEPARATOR_COLON
        return cs.SEPARATOR_DOT

    def _try_resolve_wildcard_imports(
        self, call_name: str, import_map: dict[str, str]
    ) -> tuple[str, str] | None:
        """
        Tries to resolve a call using wildcard imports (`from module import *`).

        Args:
            call_name (str): The name of the call.
            import_map (dict[str, str]): The import map for the module.

        Returns:
            tuple[str, str] | None: A tuple of (node_type, fqn) if resolved, else None.
        """
        for local_name, imported_qn in import_map.items():
            if not local_name.startswith("*"):
                continue
            if result := self._try_wildcard_qns(call_name, imported_qn):
                return result
        return None

    def _try_wildcard_qns(
        self, call_name: str, imported_qn: str
    ) -> tuple[str, str] | None:
        """
        Checks potential FQNs based on a wildcard import.

        Args:
            call_name (str): The name of the call.
            imported_qn (str): The qualified name of the wildcard-imported module.

        Returns:
            tuple[str, str] | None: A tuple of (node_type, fqn) if resolved, else None.
        """
        potential_qns = []
        if cs.SEPARATOR_DOUBLE_COLON not in imported_qn:
            potential_qns.append(f"{imported_qn}.{call_name}")
        potential_qns.append(f"{imported_qn}{cs.SEPARATOR_DOUBLE_COLON}{call_name}")

        for wildcard_qn in potential_qns:
            if wildcard_qn in self.function_registry:
                logger.debug(
                    ls.CALL_WILDCARD.format(call_name=call_name, qn=wildcard_qn)
                )
                return self.function_registry[wildcard_qn], wildcard_qn
        return None

    def _try_resolve_same_module(
        self, call_name: str, module_qn: str
    ) -> tuple[str, str] | None:
        """
        Tries to resolve a call to a function defined in the same module.

        Args:
            call_name (str): The name of the call.
            module_qn (str): The qualified name of the module.

        Returns:
            tuple[str, str] | None: A tuple of (node_type, fqn) if resolved, else None.
        """
        same_module_func_qn = f"{module_qn}.{call_name}"
        if same_module_func_qn in self.function_registry:
            logger.debug(
                ls.CALL_SAME_MODULE.format(call_name=call_name, qn=same_module_func_qn)
            )
            return self.function_registry[same_module_func_qn], same_module_func_qn
        return None

    def _try_resolve_via_trie(
        self, call_name: str, module_qn: str
    ) -> tuple[str, str] | None:
        """
        A fallback strategy to find a call target by searching the function registry trie.

        Args:
            call_name (str): The name of the call.
            module_qn (str): The qualified name of the module.

        Returns:
            tuple[str, str] | None: A tuple of (node_type, fqn) if resolved, else None.
        """
        search_name = re.split(r"[.:]|::", call_name)[-1]
        possible_matches = self.function_registry.find_ending_with(search_name)
        if not possible_matches:
            logger.debug(ls.CALL_UNRESOLVED.format(call_name=call_name))
            return None

        possible_matches.sort(
            key=lambda qn: self._calculate_import_distance(qn, module_qn)
        )
        best_candidate_qn = possible_matches[0]
        logger.debug(
            ls.CALL_TRIE_FALLBACK.format(call_name=call_name, qn=best_candidate_qn)
        )
        return self.function_registry[best_candidate_qn], best_candidate_qn

    def _resolve_two_part_call(
        self,
        parts: list[str],
        call_name: str,
        separator: str,
        import_map: dict[str, str],
        module_qn: str,
        local_var_types: dict[str, str] | None,
    ) -> tuple[str, str] | None:
        """
        Resolves a two-part qualified call (e.g., `obj.method()`).

        Args:
            parts (list[str]): The parts of the call name.
            call_name (str): The full call name.
            separator (str): The separator used.
            import_map (dict): The import map for the module.
            module_qn (str): The qualified name of the module.
            local_var_types (dict | None): A map of local variables to their types.

        Returns:
            tuple[str, str] | None: A tuple of (node_type, fqn) if resolved, else None.
        """
        object_name, method_name = parts

        if result := self._try_resolve_via_local_type(
            object_name,
            method_name,
            separator,
            call_name,
            import_map,
            module_qn,
            local_var_types,
        ):
            return result

        if result := self._try_resolve_via_import(
            object_name, method_name, separator, call_name, import_map
        ):
            return result

        return self._try_resolve_module_method(method_name, call_name, module_qn)

    def _try_resolve_via_local_type(
        self,
        object_name: str,
        method_name: str,
        separator: str,
        call_name: str,
        import_map: dict[str, str],
        module_qn: str,
        local_var_types: dict[str, str] | None,
    ) -> tuple[str, str] | None:
        """
        Tries to resolve a method call based on the inferred type of a local variable.

        Args:
            object_name (str): The name of the object/variable.
            method_name (str): The name of the method being called.
            separator (str): The separator used.
            call_name (str): The full call name.
            import_map (dict): The import map for the module.
            module_qn (str): The qualified name of the module.
            local_var_types (dict | None): A map of local variables to their types.

        Returns:
            tuple[str, str] | None: A tuple of (node_type, fqn) if resolved, else None.
        """
        if not local_var_types or object_name not in local_var_types:
            return None

        var_type = local_var_types[object_name]

        if class_qn := self._resolve_class_qn_from_type(
            var_type, import_map, module_qn
        ):
            if result := self._try_method_on_class(
                class_qn, method_name, separator, call_name, object_name, var_type
            ):
                return result

        if var_type in cs.JS_BUILTIN_TYPES:
            return (
                cs.NodeLabel.FUNCTION,
                f"{cs.BUILTIN_PREFIX}{cs.SEPARATOR_DOT}{var_type}{cs.SEPARATOR_PROTOTYPE}{method_name}",
            )
        return None

    def _try_method_on_class(
        self,
        class_qn: str,
        method_name: str,
        separator: str,
        call_name: str,
        object_name: str,
        var_type: str,
    ) -> tuple[str, str] | None:
        """
        Tries to find a method on a class, including inherited methods.

        Args:
            class_qn (str): The FQN of the class.
            method_name (str): The name of the method.
            separator (str): The separator used.
            call_name (str): The full call name.
            object_name (str): The name of the object instance.
            var_type (str): The inferred type of the object.

        Returns:
            tuple[str, str] | None: A tuple of (node_type, fqn) if resolved, else None.
        """
        method_qn = f"{class_qn}{separator}{method_name}"
        if method_qn in self.function_registry:
            logger.debug(
                ls.CALL_TYPE_INFERRED.format(
                    call_name=call_name,
                    method_qn=method_qn,
                    obj=object_name,
                    var_type=var_type,
                )
            )
            return self.function_registry[method_qn], method_qn

        if inherited := self._resolve_inherited_method(class_qn, method_name):
            logger.debug(
                ls.CALL_TYPE_INFERRED_INHERITED.format(
                    call_name=call_name,
                    method_qn=inherited[1],
                    obj=object_name,
                    var_type=var_type,
                )
            )
            return inherited
        return None

    def _try_resolve_via_import(
        self,
        object_name: str,
        method_name: str,
        separator: str,
        call_name: str,
        import_map: dict[str, str],
    ) -> tuple[str, str] | None:
        """
        Tries to resolve a static method call on an imported class/module.

        Args:
            object_name (str): The name of the imported object/module.
            method_name (str): The name of the method.
            separator (str): The separator used.
            call_name (str): The full call name.
            import_map (dict): The import map for the module.

        Returns:
            tuple[str, str] | None: A tuple of (node_type, fqn) if resolved, else None.
        """
        if object_name not in import_map:
            return None

        class_qn = self._resolve_imported_class_qn(
            import_map[object_name], object_name, method_name, separator
        )

        registry_separator = (
            separator if separator == cs.SEPARATOR_COLON else cs.SEPARATOR_DOT
        )
        method_qn = f"{class_qn}{registry_separator}{method_name}"

        if method_qn in self.function_registry:
            logger.debug(
                ls.CALL_IMPORT_STATIC.format(call_name=call_name, method_qn=method_qn)
            )
            return self.function_registry[method_qn], method_qn
        return None

    def _resolve_imported_class_qn(
        self,
        class_qn: str,
        object_name: str,
        method_name: str,
        separator: str,
    ) -> str:
        """
        Resolves the FQN of an imported class, handling language-specific cases.

        Args:
            class_qn (str): The potential FQN from the import map.
            object_name (str): The name of the object in the call.
            method_name (str): The name of the method.
            separator (str): The separator used.

        Returns:
            str: The resolved class FQN.
        """
        if cs.SEPARATOR_DOUBLE_COLON in class_qn:
            class_qn = self._resolve_rust_class_qn(class_qn)

        potential_class_qn = f"{class_qn}.{object_name}"
        test_method_qn = f"{potential_class_qn}{separator}{method_name}"
        if test_method_qn in self.function_registry:
            return potential_class_qn
        return class_qn

    def _resolve_rust_class_qn(self, class_qn: str) -> str:
        """
        Resolves a Rust class FQN, which might be ambiguous due to `::`.

        Args:
            class_qn (str): The class FQN containing `::`.

        Returns:
            str: The best-guess resolved FQN.
        """
        rust_parts = class_qn.split(cs.SEPARATOR_DOUBLE_COLON)
        class_name = rust_parts[-1]

        matching_qns = self.function_registry.find_ending_with(class_name)
        return next(
            (
                qn
                for qn in matching_qns
                if self.function_registry.get(qn) == NodeType.CLASS
            ),
            class_qn,
        )

    def _try_resolve_module_method(
        self, method_name: str, call_name: str, module_qn: str
    ) -> tuple[str, str] | None:
        """
        Tries to resolve a call as a method on the current module object.

        Args:
            method_name (str): The name of the method.
            call_name (str): The full call name.
            module_qn (str): The qualified name of the module.

        Returns:
            tuple[str, str] | None: A tuple of (node_type, fqn) if resolved, else None.
        """
        method_qn = f"{module_qn}.{method_name}"
        if method_qn in self.function_registry:
            logger.debug(
                ls.CALL_OBJECT_METHOD.format(call_name=call_name, method_qn=method_qn)
            )
            return self.function_registry[method_qn], method_qn
        return None

    def _resolve_self_attribute_call(
        self,
        parts: list[str],
        call_name: str,
        import_map: dict[str, str],
        module_qn: str,
        local_var_types: dict[str, str] | None,
    ) -> tuple[str, str] | None:
        """
        Resolves a method call on a `self` attribute (e.g., `self.service.do_work()`).

        Args:
            parts (list[str]): The parts of the call name.
            call_name (str): The full call name.
            import_map (dict): The import map for the module.
            module_qn (str): The qualified name of the module.
            local_var_types (dict | None): A map of local variables to their types.

        Returns:
            tuple[str, str] | None: A tuple of (node_type, fqn) if resolved, else None.
        """
        attribute_ref = cs.SEPARATOR_DOT.join(parts[:-1])
        method_name = parts[-1]

        if local_var_types and attribute_ref in local_var_types:
            var_type = local_var_types[attribute_ref]
            if class_qn := self._resolve_class_qn_from_type(
                var_type, import_map, module_qn
            ):
                method_qn = f"{class_qn}.{method_name}"
                if method_qn in self.function_registry:
                    logger.debug(
                        ls.CALL_INSTANCE_ATTR.format(
                            call_name=call_name,
                            method_qn=method_qn,
                            attr_ref=attribute_ref,
                            var_type=var_type,
                        )
                    )
                    return self.function_registry[method_qn], method_qn

                if inherited_method := self._resolve_inherited_method(
                    class_qn, method_name
                ):
                    logger.debug(
                        ls.CALL_INSTANCE_ATTR_INHERITED.format(
                            call_name=call_name,
                            method_qn=inherited_method[1],
                            attr_ref=attribute_ref,
                            var_type=var_type,
                        )
                    )
                    return inherited_method

        return None

    def _resolve_multi_part_call(
        self,
        parts: list[str],
        call_name: str,
        import_map: dict[str, str],
        module_qn: str,
        local_var_types: dict[str, str] | None,
    ) -> tuple[str, str] | None:
        """
        Resolves a multi-part qualified call (e.g., `a.b.c()`).

        Args:
            parts (list[str]): The parts of the call name.
            call_name (str): The full call name.
            import_map (dict): The import map for the module.
            module_qn (str): The qualified name of the module.
            local_var_types (dict | None): A map of local variables to their types.

        Returns:
            tuple[str, str] | None: A tuple of (node_type, fqn) if resolved, else None.
        """
        class_name = parts[0]
        method_name = cs.SEPARATOR_DOT.join(parts[1:])

        if class_name in import_map:
            class_qn = import_map[class_name]
            method_qn = f"{class_qn}.{method_name}"
            if method_qn in self.function_registry:
                logger.debug(
                    ls.CALL_IMPORT_QUALIFIED.format(
                        call_name=call_name, method_qn=method_qn
                    )
                )
                return self.function_registry[method_qn], method_qn

        if local_var_types and class_name in local_var_types:
            var_type = local_var_types[class_name]
            if class_qn := self._resolve_class_qn_from_type(
                var_type, import_map, module_qn
            ):
                method_qn = f"{class_qn}.{method_name}"
                if method_qn in self.function_registry:
                    logger.debug(
                        ls.CALL_INSTANCE_QUALIFIED.format(
                            call_name=call_name,
                            method_qn=method_qn,
                            class_name=class_name,
                            var_type=var_type,
                        )
                    )
                    return self.function_registry[method_qn], method_qn

                if inherited_method := self._resolve_inherited_method(
                    class_qn, method_name
                ):
                    logger.debug(
                        ls.CALL_INSTANCE_INHERITED.format(
                            call_name=call_name,
                            method_qn=inherited_method[1],
                            class_name=class_name,
                            var_type=var_type,
                        )
                    )
                    return inherited_method

        return None

    def resolve_builtin_call(self, call_name: str) -> tuple[str, str] | None:
        """
        Resolves a call to a known built-in function (e.g., in JavaScript).

        Args:
            call_name (str): The name of the call.

        Returns:
            tuple[str, str] | None: A tuple of (node_type, fqn) if resolved, else None.
        """
        if call_name in cs.JS_BUILTIN_PATTERNS:
            return (cs.NodeLabel.FUNCTION, f"{cs.BUILTIN_PREFIX}.{call_name}")

        for suffix, method in cs.JS_FUNCTION_PROTOTYPE_SUFFIXES.items():
            if call_name.endswith(suffix):
                return (
                    cs.NodeLabel.FUNCTION,
                    f"{cs.BUILTIN_PREFIX}{cs.SEPARATOR_DOT}Function{cs.SEPARATOR_PROTOTYPE}{method}",
                )

        if cs.SEPARATOR_PROTOTYPE in call_name and (
            call_name.endswith(cs.JS_SUFFIX_CALL)
            or call_name.endswith(cs.JS_SUFFIX_APPLY)
        ):
            base_call = call_name.rsplit(cs.SEPARATOR_DOT, 1)[0]
            return (cs.NodeLabel.FUNCTION, base_call)

        return None

    def resolve_cpp_operator_call(
        self, call_name: str, module_qn: str
    ) -> tuple[str, str] | None:
        """
        Resolves a C++ operator overload call.

        Args:
            call_name (str): The name of the operator function (e.g., 'operator_plus').
            module_qn (str): The qualified name of the module.

        Returns:
            tuple[str, str] | None: A tuple of (node_type, fqn) if resolved, else None.
        """
        if not call_name.startswith(cs.OPERATOR_PREFIX):
            return None

        if call_name in cs.CPP_OPERATORS:
            return (cs.NodeLabel.FUNCTION, cs.CPP_OPERATORS[call_name])

        if possible_matches := self.function_registry.find_ending_with(call_name):
            same_module_ops = [
                qn
                for qn in possible_matches
                if qn.startswith(module_qn) and call_name in qn
            ]
            candidates = same_module_ops or possible_matches
            candidates.sort(key=lambda qn: (len(qn), qn))
            best = candidates[0]
            return (self.function_registry[best], best)

        return None

    def _is_method_chain(self, call_name: str) -> bool:
        """Checks if a call name represents a chained method call."""
        if cs.CHAR_PAREN_OPEN not in call_name or cs.CHAR_PAREN_CLOSE not in call_name:
            return False
        parts = call_name.split(cs.SEPARATOR_DOT)
        method_calls = sum(
            cs.CHAR_PAREN_OPEN in part and cs.CHAR_PAREN_CLOSE in part for part in parts
        )
        return method_calls >= 1 and len(parts) >= 2

    def _resolve_chained_call(
        self,
        call_name: str,
        module_qn: str,
        local_var_types: dict[str, str] | None = None,
    ) -> tuple[str, str] | None:
        """
        Resolves the final call in a method chain (e.g., `a().b().c()`).

        Args:
            call_name (str): The full chained call string.
            module_qn (str): The qualified name of the module.
            local_var_types (dict | None): A map of local variables to their types.

        Returns:
            tuple[str, str] | None: A tuple of (node_type, fqn) if resolved, else None.
        """
        match = re.search(r"\.([^.()]+)$", call_name)
        if not match:
            return None

        final_method = match[1]

        object_expr = call_name[: match.start()]

        if (
            object_type
            := self.type_inference.python_type_inference._infer_expression_return_type(
                object_expr, module_qn, local_var_types
            )
        ):
            full_object_type = object_type
            if cs.SEPARATOR_DOT not in object_type:
                if resolved_class := self._resolve_class_name(object_type, module_qn):
                    full_object_type = resolved_class

            method_qn = f"{full_object_type}.{final_method}"

            if method_qn in self.function_registry:
                logger.debug(
                    ls.CALL_CHAINED.format(
                        call_name=call_name,
                        method_qn=method_qn,
                        obj_expr=object_expr,
                        obj_type=object_type,
                    )
                )
                return self.function_registry[method_qn], method_qn

            if inherited_method := self._resolve_inherited_method(
                full_object_type, final_method
            ):
                logger.debug(
                    ls.CALL_CHAINED_INHERITED.format(
                        call_name=call_name,
                        method_qn=inherited_method[1],
                        obj_expr=object_expr,
                        obj_type=object_type,
                    )
                )
                return inherited_method

        return None

    def _resolve_super_call(
        self, call_name: str, class_context: str | None = None
    ) -> tuple[str, str] | None:
        """
        Resolves a `super()` call to the appropriate method in a parent class.

        Args:
            call_name (str): The `super()` call string.
            class_context (str | None): The FQN of the class where the call occurs.

        Returns:
            tuple[str, str] | None: A tuple of (node_type, fqn) if resolved, else None.
        """
        match call_name:
            case _ if call_name == cs.KEYWORD_SUPER:
                method_name = cs.KEYWORD_CONSTRUCTOR
            case _ if cs.SEPARATOR_DOT in call_name:
                method_name = call_name.split(cs.SEPARATOR_DOT, 1)[1]
            case _:
                return None

        current_class_qn = class_context
        if not current_class_qn:
            logger.debug(ls.CALL_SUPER_NO_CONTEXT.format(call_name=call_name))
            return None

        if current_class_qn not in self.class_inheritance:
            logger.debug(ls.CALL_SUPER_NO_INHERITANCE.format(class_qn=current_class_qn))
            return None

        parent_classes = self.class_inheritance[current_class_qn]
        if not parent_classes:
            logger.debug(ls.CALL_SUPER_NO_PARENTS.format(class_qn=current_class_qn))
            return None

        if result := self._resolve_inherited_method(current_class_qn, method_name):
            callee_type, parent_method_qn = result
            logger.debug(
                ls.CALL_SUPER_RESOLVED.format(
                    call_name=call_name, method_qn=parent_method_qn
                )
            )
            return callee_type, parent_method_qn

        logger.debug(
            ls.CALL_SUPER_UNRESOLVED.format(
                call_name=call_name, class_qn=current_class_qn
            )
        )
        return None

    def _resolve_inherited_method(
        self, class_qn: str, method_name: str
    ) -> tuple[str, str] | None:
        """
        Recursively searches parent classes for a method.

        Args:
            class_qn (str): The FQN of the starting class.
            method_name (str): The name of the method to find.

        Returns:
            tuple[str, str] | None: A tuple of (node_type, fqn) if found, else None.
        """
        if class_qn not in self.class_inheritance:
            return None

        queue = list(self.class_inheritance.get(class_qn, []))
        visited = set(queue)

        while queue:
            parent_class_qn = queue.pop(0)
            parent_method_qn = f"{parent_class_qn}.{method_name}"

            if parent_method_qn in self.function_registry:
                return (
                    self.function_registry[parent_method_qn],
                    parent_method_qn,
                )

            if parent_class_qn in self.class_inheritance:
                for grandparent_qn in self.class_inheritance[parent_class_qn]:
                    if grandparent_qn not in visited:
                        visited.add(grandparent_qn)
                        queue.append(grandparent_qn)

        return None

    def _calculate_import_distance(
        self, candidate_qn: str, caller_module_qn: str
    ) -> int:
        """
        Calculates a 'distance' score between two qualified names.

        Used to rank potential matches from the trie search, preferring closer
        matches in the module hierarchy.

        Args:
            candidate_qn (str): The FQN of the potential callee.
            caller_module_qn (str): The FQN of the module containing the call.

        Returns:
            int: The calculated distance score.
        """
        caller_parts = caller_module_qn.split(cs.SEPARATOR_DOT)
        candidate_parts = candidate_qn.split(cs.SEPARATOR_DOT)

        common_prefix = 0
        for i in range(min(len(caller_parts), len(candidate_parts))):
            if caller_parts[i] == candidate_parts[i]:
                common_prefix += 1
            else:
                break

        base_distance = max(len(caller_parts), len(candidate_parts)) - common_prefix

        if candidate_qn.startswith(
            cs.SEPARATOR_DOT.join(caller_parts[:-1]) + cs.SEPARATOR_DOT
        ):
            base_distance -= 1

        return base_distance

    def _resolve_class_name(self, class_name: str, module_qn: str) -> str | None:
        """
        Resolves a simple class name to its FQN within a module context.

        Args:
            class_name (str): The simple name of the class.
            module_qn (str): The FQN of the module.

        Returns:
            str | None: The resolved FQN of the class, or None.
        """
        return resolve_class_name(
            class_name, module_qn, self.import_processor, self.function_registry
        )

    def resolve_java_method_call(
        self,
        call_node: Node,
        module_qn: str,
        local_var_types: dict[str, str],
    ) -> tuple[str, str] | None:
        """
        Resolves a Java method call using the Java-specific type inference engine.

        Args:
            call_node (Node): The method invocation node.
            module_qn (str): The FQN of the module.
            local_var_types (dict[str, str]): A map of local variables to their types.

        Returns:
            tuple[str, str] | None: A tuple of (node_type, fqn) if resolved, else None.
        """
        java_engine = self.type_inference.java_type_inference

        result = java_engine.resolve_java_method_call(
            call_node, local_var_types, module_qn
        )

        if result:
            call_text = (
                call_node.text.decode(cs.ENCODING_UTF8)
                if call_node.text
                else cs.TEXT_UNKNOWN
            )
            logger.debug(
                ls.CALL_JAVA_RESOLVED.format(call_text=call_text, method_qn=result[1])
            )

        return result
