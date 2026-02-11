from __future__ import annotations

import re
from typing import Any

from codebase_rag.core import constants as cs

from ...utils.source_extraction import extract_source_lines
from .base_module import AnalysisContext, AnalysisModule


class PerformanceAnalysisModule(AnalysisModule):
    def get_name(self) -> str:
        return "performance_analysis"

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        if not context.nodes:
            return {}

        issues: list[dict[str, object]] = []
        scanned = 0
        total_lines = 0
        max_lines = 0
        max_loops = 0
        nested_loops_count = 0
        io_in_loop_count = 0
        large_param_count = 0
        loop_tokens = {"for", "while", "foreach"}
        io_markers = (
            "open(",
            ".read(",
            ".write(",
            "requests.",
            "http.",
            "fetch(",
            "axios.",
            "cursor.execute",
            "select ",
            "insert ",
            "update ",
            "delete ",
            "query(",
            "db.",
        )

        for node in context.nodes:
            if (
                cs.NodeLabel.FUNCTION.value not in node.labels
                and cs.NodeLabel.METHOD.value not in node.labels
            ):
                continue

            qualified_name = str(node.properties.get(cs.KEY_QUALIFIED_NAME) or "")
            function_name = str(node.properties.get(cs.KEY_NAME) or "")
            path = context.runner._resolve_node_path(node, context.module_path_map)
            start_line = int(str(node.properties.get(cs.KEY_START_LINE) or 0))
            end_line = int(str(node.properties.get(cs.KEY_END_LINE) or 0))
            if not path or not start_line or not end_line:
                continue

            source = extract_source_lines(
                context.runner.repo_path / path, start_line, end_line
            )
            if not source:
                continue

            lines = source.splitlines()
            scanned += 1
            line_count = len(lines)
            total_lines += line_count
            max_lines = max(max_lines, line_count)
            if line_count >= 200:
                issues.append(
                    {
                        "type": "large_function",
                        "severity": "high" if line_count >= 400 else "medium",
                        "qualified_name": qualified_name,
                        "path": path,
                        "lines": line_count,
                    }
                )

            loop_count = 0
            io_hits = 0
            loop_indents: list[int] = []
            for line in lines:
                tokens = re.split(r"\W+", line)
                if any(token in loop_tokens for token in tokens if token):
                    loop_count += 1
                    loop_indents.append(len(line) - len(line.lstrip()))
                if any(marker in line for marker in io_markers):
                    io_hits += 1
            max_loops = max(max_loops, loop_count)

            if len(loop_indents) >= 2 and (max(loop_indents) - min(loop_indents)) >= 4:
                issues.append(
                    {
                        "type": "nested_loops",
                        "severity": "high" if loop_count >= 4 else "medium",
                        "qualified_name": qualified_name,
                        "path": path,
                        "loops": loop_count,
                    }
                )
                nested_loops_count += 1

            if loop_count >= 4:
                issues.append(
                    {
                        "type": "loop_heavy",
                        "severity": "medium",
                        "qualified_name": qualified_name,
                        "path": path,
                        "loops": loop_count,
                    }
                )

            if loop_count and io_hits:
                issues.append(
                    {
                        "type": "io_in_loop",
                        "severity": "high",
                        "qualified_name": qualified_name,
                        "path": path,
                        "loops": loop_count,
                        "io_markers": io_hits,
                    }
                )
                io_in_loop_count += 1

            parameters = node.properties.get(cs.KEY_PARAMETERS)
            param_count = len(parameters) if isinstance(parameters, list) else 0
            if param_count >= 6:
                issues.append(
                    {
                        "type": "large_parameter_list",
                        "severity": "medium",
                        "qualified_name": qualified_name,
                        "path": path,
                        "parameters": param_count,
                    }
                )
                large_param_count += 1

            if function_name:
                recursion_hits = source.count(f"{function_name}(")
                if recursion_hits > 1:
                    issues.append(
                        {
                            "type": "possible_recursion",
                            "severity": "medium",
                            "qualified_name": qualified_name,
                            "path": path,
                            "hits": recursion_hits,
                        }
                    )

        payload = {
            "functions_scanned": scanned,
            "issues": len(issues),
            "top_issues": issues[:50],
            "avg_lines": round(total_lines / scanned, 2) if scanned else 0.0,
            "max_lines": max_lines,
            "max_loops": max_loops,
            "nested_loops": nested_loops_count,
            "io_in_loop": io_in_loop_count,
            "large_parameter_lists": large_param_count,
        }
        context.runner._write_json_report(
            "performance_report.json",
            {"summary": payload, "issues": issues},
        )
        return payload
