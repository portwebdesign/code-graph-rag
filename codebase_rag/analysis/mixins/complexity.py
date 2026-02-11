from __future__ import annotations

import re

from codebase_rag.core import constants as cs

from ...utils.source_extraction import extract_source_lines
from ..protocols import AnalysisRunnerProtocol
from ..types import NodeRecord


class ComplexityMixin:
    def _compute_complexity(
        self: AnalysisRunnerProtocol,
        nodes: list[NodeRecord],
        module_path_map: dict[str, str],
    ) -> dict[str, float]:
        total = 0
        max_complexity = 0
        measured = 0
        decision_keywords = {
            "if",
            "elif",
            "else",
            "for",
            "while",
            "do",
            "case",
            "catch",
            "switch",
            "when",
            "match",
        }
        for node in nodes:
            if (
                cs.NodeLabel.FUNCTION.value not in node.labels
                and cs.NodeLabel.METHOD.value not in node.labels
            ):
                continue

            qn = str(node.properties.get(cs.KEY_QUALIFIED_NAME) or "")
            path = self._resolve_node_path(node, module_path_map)
            start_line = int(str(node.properties.get(cs.KEY_START_LINE) or 0))
            end_line = int(str(node.properties.get(cs.KEY_END_LINE) or 0))
            if not path or not start_line or not end_line:
                continue

            source = extract_source_lines(self.repo_path / path, start_line, end_line)
            if not source:
                continue

            lines = source.splitlines()
            complexity = 1
            for line in lines:
                tokens = re.split(r"\W+", line)
                if any(token in decision_keywords for token in tokens):
                    complexity += 1
                if "&&" in line or "||" in line or "??" in line:
                    complexity += 1
                if "?" in line and ":" in line:
                    complexity += 1

            self.ingestor.ensure_node_batch(
                (
                    cs.NodeLabel.METHOD
                    if cs.NodeLabel.METHOD.value in node.labels
                    else cs.NodeLabel.FUNCTION
                ),
                {
                    cs.KEY_QUALIFIED_NAME: qn,
                    "complexity": complexity,
                    "lines_of_code": end_line - start_line + 1,
                },
            )
            total += complexity
            max_complexity = max(max_complexity, complexity)
            measured += 1

        average = total / measured if measured else 0.0
        return {
            "average": average,
            "max": float(max_complexity),
            "count": float(measured),
        }
