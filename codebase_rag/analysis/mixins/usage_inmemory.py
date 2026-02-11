from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any, cast

from loguru import logger

from codebase_rag.core import constants as cs

from ..protocols import AnalysisRunnerProtocol
from ..types import NodeRecord, RelationshipRecord


class UsageInMemoryMixin:
    def _symbol_usage(
        self: AnalysisRunnerProtocol,
        nodes: list[NodeRecord],
        relationships: list[RelationshipRecord],
        node_by_id: dict[int, NodeRecord],
    ) -> dict[str, int]:
        usage_counts: dict[int, int] = {}
        for rel in relationships:
            if rel.rel_type in {
                cs.RelationshipType.CALLS,
                cs.RelationshipType.USES_COMPONENT,
                cs.RelationshipType.REQUESTS_ENDPOINT,
                cs.RelationshipType.RESOLVES_IMPORT,
                cs.RelationshipType.USES_ASSET,
                cs.RelationshipType.HANDLES_ERROR,
                cs.RelationshipType.MUTATES_STATE,
            }:
                usage_counts[rel.to_id] = usage_counts.get(rel.to_id, 0) + 1

        for node_id, count in usage_counts.items():
            node = node_by_id.get(node_id)
            if not node:
                continue
            qn = str(node.properties.get(cs.KEY_QUALIFIED_NAME) or "")
            if not qn:
                continue
            label = self._primary_label(node)
            if not label:
                continue
            self.ingestor.ensure_node_batch(
                label,
                {
                    cs.KEY_QUALIFIED_NAME: qn,
                    "usage_count": count,
                },
            )

        return {
            "symbols_with_usage": len(usage_counts),
            "total_usage_edges": sum(usage_counts.values()) if usage_counts else 0,
        }

    def _dead_code_report(
        self: AnalysisRunnerProtocol,
        nodes: list[NodeRecord],
        relationships: list[RelationshipRecord],
        node_by_id: dict[int, NodeRecord],
    ) -> dict[str, Any]:
        entry_points = {
            "main",
            "__main__",
            "index",
            "app",
            "server",
            "start",
            "run",
            "init",
            "initialize",
            "bootstrap",
            "setup",
            "configure",
            "render",
            "default",
        }
        decorators = {
            "@route",
            "@controller",
            "@component",
            "@injectable",
            "@public",
        }

        call_graph: dict[int, set[int]] = {}
        for rel in relationships:
            if rel.rel_type != cs.RelationshipType.CALLS:
                continue
            call_graph.setdefault(rel.from_id, set()).add(rel.to_id)

        reachable: set[int] = set()

        def mark(node_id: int) -> None:
            if node_id in reachable:
                return
            reachable.add(node_id)
            for target in call_graph.get(node_id, set()):
                mark(target)

        for node in nodes:
            if (
                cs.NodeLabel.FUNCTION.value not in node.labels
                and cs.NodeLabel.METHOD.value not in node.labels
            ):
                continue
            name = str(node.properties.get(cs.KEY_NAME) or "")
            is_exported = bool(node.properties.get(cs.KEY_IS_EXPORTED))
            node_decorators = set(
                cast(Iterable[Any], node.properties.get(cs.KEY_DECORATORS) or [])
            )
            if name in entry_points or is_exported or (node_decorators & decorators):
                mark(node.node_id)

        dead_nodes = [
            node
            for node in nodes
            if (
                cs.NodeLabel.FUNCTION.value in node.labels
                or cs.NodeLabel.METHOD.value in node.labels
            )
            and node.node_id not in reachable
        ]

        report = {
            "total_functions": len(
                [
                    node
                    for node in nodes
                    if cs.NodeLabel.FUNCTION.value in node.labels
                    or cs.NodeLabel.METHOD.value in node.labels
                ]
            ),
            "dead_functions": [
                {
                    "qualified_name": node.properties.get(cs.KEY_QUALIFIED_NAME),
                    "name": node.properties.get(cs.KEY_NAME),
                    "path": node.properties.get(cs.KEY_PATH),
                    "start_line": node.properties.get(cs.KEY_START_LINE),
                }
                for node in dead_nodes
            ],
        }

        output_dir = self.repo_path / "output" / "analysis"
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "dead_code_report.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        logger.info("Dead code report saved: {}", report_path)

        return {
            "total_functions": report["total_functions"],
            "dead_functions": report["dead_functions"],
        }

    def _unused_imports(
        self: AnalysisRunnerProtocol,
        nodes: list[NodeRecord],
        file_paths: list[str] | None = None,
    ) -> dict[str, int]:
        file_paths = file_paths or self._collect_file_paths(nodes)
        findings: list[dict[str, object]] = []

        def record_unused(path: str, name: str, usages: int) -> None:
            findings.append({"path": path, "name": name, "usages": usages})

        def extract_imports_py(content: str) -> list[str]:
            results: list[str] = []
            for match in re.finditer(r"^\s*import\s+([^#\n]+)", content, re.MULTILINE):
                parts = match.group(1).split(",")
                for part in parts:
                    token = part.strip().split(" as ")[0].strip()
                    if token:
                        results.append(token)
            for match in re.finditer(
                r"^\s*from\s+[\w.]+\s+import\s+([^#\n]+)",
                content,
                re.MULTILINE,
            ):
                parts = match.group(1).split(",")
                for part in parts:
                    token = part.strip().split(" as ")[0].strip()
                    if token and token != "*":
                        results.append(token)
            return results

        def extract_imports_js(content: str) -> list[str]:
            results: list[str] = []
            for match in re.finditer(
                r"^\s*import\s+(.+?)\s+from\s+['\"][^'\"]+['\"]",
                content,
                re.MULTILINE,
            ):
                clause = match.group(1).strip()
                clause = clause.replace("{", "").replace("}", "")
                for part in clause.split(","):
                    token = part.strip().split(" as ")[-1].strip()
                    if token and token not in {"*", "type"}:
                        results.append(token)
            for match in re.finditer(
                r"^\s*const\s+(\w+)\s*=\s*require\(",
                content,
                re.MULTILINE,
            ):
                results.append(match.group(1))
            return results

        for path in file_paths:
            file_path = self.repo_path / path
            if not file_path.exists() or file_path.stat().st_size > 1_000_000:
                continue
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            suffix = file_path.suffix.lower()
            if suffix in {".py"}:
                imports = extract_imports_py(content)
            elif suffix in {".js", ".jsx", ".ts", ".tsx"}:
                imports = extract_imports_js(content)
            else:
                continue

            for name in imports:
                usages = len(re.findall(rf"\b{re.escape(name)}\b", content))
                if usages <= 1:
                    record_unused(path, name, usages)

        output_dir = self.repo_path / "output" / "analysis"
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "unused_imports_report.json"
        report_path.write_text(json.dumps(findings, indent=2), encoding="utf-8")
        return {
            "unused_imports": len(findings),
            "files_with_unused": len({item["path"] for item in findings}),
        }
