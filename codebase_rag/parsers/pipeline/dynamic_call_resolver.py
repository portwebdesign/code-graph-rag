from __future__ import annotations

import re
from collections.abc import Iterable

from codebase_rag.core import constants as cs
from codebase_rag.data_models.types_defs import FunctionRegistryTrieProtocol, NodeType


class DynamicCallResolver:
    """
    Resolves dynamic function calls from string snippets or other non-standard call sites.

    This class parses potential function or action names from strings (e.g., in routes, specific attributes)
    and attempts to match them against the known function registry.

    Args:
        function_registry (FunctionRegistryTrieProtocol): Registry of known functions/classes.
    """

    def __init__(self, function_registry: FunctionRegistryTrieProtocol) -> None:
        self.function_registry = function_registry

    def resolve_from_snippet(
        self, snippet: str, module_qn: str
    ) -> tuple[NodeType, str] | None:
        """
        Attempts to resolve a function call from a code snippet.

        Args:
            snippet (str): The code snippet to analyze.
            module_qn (str): The module qualified name where the snippet is located.

        Returns:
            tuple[NodeType, str] | None: A tuple of (node_type, qualified_name) if resolved, else None.
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
        Extracts potential function/action names from a snippet using various patterns.

        Args:
            snippet (str): The code snippet.

        Returns:
            list[str]: A deduplicated list of candidate names.
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
        Extracts identifiers found inside quotes (single or double).

        Args:
            snippet (str): The code snippet.

        Returns:
            Iterable[str]: Matched identifiers.
        """
        pattern = r"['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]"
        return re.findall(pattern, snippet)

    @staticmethod
    def _extract_route_action_names(snippet: str) -> Iterable[str]:
        """
        Extracts identifiers prefixed with @ (common in some routing frameworks).

        Args:
            snippet (str): The code snippet.

        Returns:
            Iterable[str]: Matched action names.
        """
        pattern = r"@([A-Za-z_][A-Za-z0-9_]*)"
        return re.findall(pattern, snippet)

    @staticmethod
    def _extract_bracket_members(snippet: str) -> Iterable[str]:
        """
        Extracts identifiers inside brackets and quotes (e.g., keys in dictionaries/arrays).

        Args:
            snippet (str): The code snippet.

        Returns:
            Iterable[str]: Matched member names.
        """
        pattern = r"\[\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*\]"
        return re.findall(pattern, snippet)

    @staticmethod
    def _dedupe_preserve_order(items: list[str]) -> list[str]:
        """
        Deduplicates a list while preserving the original order of elements.

        Args:
            items (list[str]): The input list.

        Returns:
            list[str]: The deduplicated list.
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
        Prefers candidates in the same module/namespace.

        Args:
            candidates (list[str]): List of candidate qualified names.
            module_qn (str): The current module context.

        Returns:
            str: The best matching qualified name.
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
        Calculates a distance metric for tie-breaking based on module path similarity.

        Args:
            candidate_qn (str): Candidate qualified name.
            module_qn (str): Current module qualified name.

        Returns:
            int: Distance score (lower is better).
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
