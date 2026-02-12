"""
This module defines the `DynamicCallResolver`, a utility for resolving function
calls that are not standard, direct call expressions in the source code.

This is particularly useful for modern frameworks where functionality is often
invoked through string names, such as in routing decorators, event listeners, or
configuration objects. The resolver uses a series of regular expressions to
extract potential function names from string literals and other patterns within
a code snippet. It then attempts to match these candidate names against the
known function registry to find the most likely target.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from codebase_rag.core import constants as cs
from codebase_rag.data_models.types_defs import FunctionRegistryTrieProtocol, NodeType


class DynamicCallResolver:
    """
    Resolves dynamic function calls from string snippets or other non-standard call sites.

    This class is designed to handle cases where a function is called indirectly,
    for example, by its string name. It parses code snippets to find potential
    function names and matches them against a registry of known functions.
    """

    def __init__(self, function_registry: FunctionRegistryTrieProtocol) -> None:
        """
        Initializes the DynamicCallResolver.

        Args:
            function_registry (FunctionRegistryTrieProtocol): A registry (typically a Trie)
                containing all known functions and classes in the codebase.
        """
        self.function_registry = function_registry

    def resolve_from_snippet(
        self, snippet: str, module_qn: str
    ) -> tuple[NodeType, str] | None:
        """
        Attempts to resolve a function call from a given code snippet.

        It extracts candidate names from the snippet and searches for the best match
        in the function registry.

        Args:
            snippet (str): The code snippet to analyze (e.g., a decorator, a dictionary).
            module_qn (str): The qualified name of the module where the snippet is located,
                             used for context in ranking candidates.

        Returns:
            A tuple of (node_type, qualified_name) if a likely match is found, otherwise None.
        """
        for candidate in self._extract_candidate_names(snippet):
            matches = self.function_registry.find_ending_with(candidate)
            if not matches:
                continue
            best = self._select_best_candidate(matches, module_qn)
            return self.function_registry[best], best
        return None

    def _extract_candidate_names(self, snippet: str) -> list[str]:
        """
        Extracts potential function or action names from a snippet using various regex patterns.

        This method applies several heuristics to find strings that might represent
        a function name.

        Args:
            snippet (str): The code snippet to scan.

        Returns:
            A deduplicated list of candidate names found in the snippet.
        """
        candidates: list[str] = []
        for name in self._extract_string_identifiers(snippet):
            candidates.append(name)
        for name in self._extract_route_action_names(snippet):
            candidates.append(name)
        for name in self._extract_bracket_members(snippet):
            candidates.append(name)
        return self._dedupe_preserve_order(candidates)

    @staticmethod
    def _extract_string_identifiers(snippet: str) -> Iterable[str]:
        """
        Extracts valid identifiers found inside single or double quotes.

        Args:
            snippet (str): The code snippet.

        Returns:
            An iterable of matched identifiers.
        """
        pattern = r"['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]"
        return re.findall(pattern, snippet)

    @staticmethod
    def _extract_route_action_names(snippet: str) -> Iterable[str]:
        """
        Extracts identifiers that look like route actions (e.g., "Controller@action").

        Args:
            snippet (str): The code snippet.

        Returns:
            An iterable of matched action names.
        """
        pattern = r"@([A-Za-z_][A-Za-z0-9_]*)"
        return re.findall(pattern, snippet)

    @staticmethod
    def _extract_bracket_members(snippet: str) -> Iterable[str]:
        """
        Extracts identifiers used as keys in bracket notation (e.g., `obj['key']`).

        Args:
            snippet (str): The code snippet.

        Returns:
            An iterable of matched member names.
        """
        pattern = r"\[\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*\]"
        return re.findall(pattern, snippet)

    @staticmethod
    def _dedupe_preserve_order(items: list[str]) -> list[str]:
        """
        Deduplicates a list of strings while preserving their original order.

        Args:
            items (list[str]): The input list of strings.

        Returns:
            A new list containing the unique items in their original order.
        """
        seen: set[str] = set()
        deduped: list[str] = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    def _select_best_candidate(self, candidates: list[str], module_qn: str) -> str:
        """
        Selects the best matching qualified name from a list of candidates.

        The selection prefers candidates that are in the same module or a parent
        module of the current context, using a path distance metric as a tie-breaker.

        Args:
            candidates (list[str]): A list of candidate fully qualified names.
            module_qn (str): The qualified name of the current module for context.

        Returns:
            The best matching qualified name from the list.
        """
        preferred = [
            qn for qn in candidates if qn.startswith(f"{module_qn}{cs.SEPARATOR_DOT}")
        ]
        if preferred:
            return preferred[0]
        return min(candidates, key=lambda qn: self._distance(qn, module_qn))

    @staticmethod
    def _distance(candidate_qn: str, module_qn: str) -> int:
        """
        Calculates a simple distance metric between two qualified names.

        The distance is based on the number of non-common parts in their paths,
        which helps in prioritizing closer matches in the module hierarchy.

        Args:
            candidate_qn (str): The qualified name of the candidate function.
            module_qn (str): The qualified name of the module where the call occurs.

        Returns:
            An integer distance score, where a lower score indicates a closer match.
        """
        caller_parts = module_qn.split(cs.SEPARATOR_DOT)
        candidate_parts = candidate_qn.split(cs.SEPARATOR_DOT)

        common_prefix = 0
        for i in range(min(len(caller_parts), len(candidate_parts))):
            if caller_parts[i] == candidate_parts[i]:
                common_prefix += 1
            else:
                break

        return max(len(caller_parts), len(candidate_parts)) - common_prefix
