from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RefactoringSuggestion:
    name: str
    description: str = ""


class RefactoringAssistant:
    def _fetch_source(self, node_id: int) -> str:
        _ = node_id
        return ""

    def suggest_refactorings(self, node_id: int) -> list[RefactoringSuggestion]:
        source = self._fetch_source(node_id)
        lines = source.splitlines()
        suggestions: list[RefactoringSuggestion] = []
        if len(lines) >= 50:
            suggestions.append(RefactoringSuggestion(name="extract_method"))
        return suggestions
