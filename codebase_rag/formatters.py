from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any


class ReportFormatter:
    @staticmethod
    def format_json_to_html(
        data: Any, title: str = "Graph-Code Analysis Report"
    ) -> str:
        try:
            payload = json.loads(data) if isinstance(data, str) else data
        except Exception:
            return (
                "<!DOCTYPE html>"
                "<html><head><title>Error Generating Report</title></head>"
                "<body><h1>Error Generating Report</h1><p>Invalid JSON</p></body>"
                "</html>"
            )

        pretty = json.dumps(payload, indent=2, ensure_ascii=False)
        escaped = html.escape(pretty)
        return (
            "<!DOCTYPE html>"
            "<html><head><title>"
            + html.escape(title)
            + "</title></head><body>"
            + "<h1>"
            + html.escape(title)
            + "</h1><pre>"
            + escaped
            + "</pre></body></html>"
        )

    @staticmethod
    def save_html_report(
        data: Any, output_path: str | Path, title: str = "Graph-Code Analysis Report"
    ) -> Path:
        path = Path(output_path)
        html_content = ReportFormatter.format_json_to_html(data, title=title)
        path.write_text(html_content, encoding="utf-8")
        return path


class MermaidProjectDiagramGenerator:
    @staticmethod
    def _clean_id(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value)
        return cleaned[:50]

    @staticmethod
    def generate_project_structure_diagram(graph_data: dict[str, Any]) -> str:
        lines = ["graph TD"]
        for node in graph_data.get("results", []):
            name = str(node.get("name") or node.get("qualified_name") or "")
            if not name:
                continue
            node_id = MermaidProjectDiagramGenerator._clean_id(
                str(node.get("qualified_name") or name)
            )
            lines.append(f'    {node_id}["{name}"]')
        return "\n".join(lines)

    @staticmethod
    def generate_llm_integration_diagram() -> str:
        return "\n".join(
            [
                "graph TD",
                '    User["User Query"] --> LLM["LLM"]',
                '    LLM --> CypherGenerator["CypherGenerator"]',
                '    CypherGenerator --> Graph["Graph DB"]',
                "    Graph --> LLM",
                '    LLM --> Feedback["Retry with Feedback"]',
            ]
        )
