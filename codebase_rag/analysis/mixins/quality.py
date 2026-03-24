from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import TypedDict

from codebase_rag.core import constants as cs

from ...utils.source_extraction import extract_source_lines
from ..protocols import AnalysisRunnerProtocol
from ..types import NodeRecord


class QualityMixin:
    _LOW_VALUE_DUPLICATE_NAME_RE = re.compile(
        r"^_?(repo_root|load_json|read_json|write_json|timestamp|read_text|write_text)$"
    )
    _LOW_VALUE_DUPLICATE_PATH_MARKERS = {
        "cli",
        "governance",
        "script",
        "scripts",
        "tool",
        "tools",
    }

    @staticmethod
    def _should_skip_duplicate_path(path: str) -> bool:
        normalized = path.replace("\\", "/").strip("/").lower()
        if not normalized:
            return True
        parts = [part for part in normalized.split("/") if part]
        include_tests = str(
            os.getenv("CODEGRAPH_ANALYSIS_INCLUDE_TESTS", "")
        ).lower() in {
            "1",
            "true",
            "yes",
        }
        skip_parts = {
            "output",
            "build",
            "dist",
            "node_modules",
            "agent-logs",
            "__pycache__",
            "htmlcov",
            "memgraph_logs",
            ".venv",
            "venv",
        }
        if not include_tests:
            skip_parts |= {"test", "tests", "examples", "docs"}
        return any(part in skip_parts for part in parts)

    @staticmethod
    def _normalize_duplicate_source(source: str) -> str:
        without_line_comments = re.sub(r"//.*", "", source)
        without_hash_comments = re.sub(r"#.*", "", without_line_comments)
        return re.sub(r"\s+", "", without_hash_comments)

    @staticmethod
    def _is_synthetic_duplicate_name(name: str) -> bool:
        return (
            name.startswith("anonymous_")
            or name.startswith(cs.IIFE_ARROW_PREFIX)
            or name.startswith(cs.IIFE_FUNC_PREFIX)
        )

    @classmethod
    def _is_low_value_duplicate_item(cls, name: str, path: str) -> bool:
        normalized_path = path.replace("\\", "/").lower()
        path_parts = {part for part in normalized_path.split("/") if part}
        return bool(cls._LOW_VALUE_DUPLICATE_NAME_RE.match(name)) and bool(
            path_parts & cls._LOW_VALUE_DUPLICATE_PATH_MARKERS
        )

    @classmethod
    def _classify_duplicate_group(
        cls,
        items: list[dict[str, object]],
        normalized_length: int,
    ) -> tuple[str, str, bool, str]:
        names = [str(item.get("name") or "") for item in items]
        paths = [str(item.get("path") or "") for item in items]
        unique_paths = {path for path in paths if path}

        if names and all(cls._is_synthetic_duplicate_name(name) for name in names):
            return (
                "anonymous_callback",
                "low",
                False,
                "Synthetic or anonymous callback bodies are low-value duplicate noise.",
            )
        if len(unique_paths) == 1:
            return (
                "same_file_overlap",
                "low",
                False,
                "Same-file duplicate groups are typically local extraction candidates, not cross-file action items.",
            )
        if names and all(
            cls._is_low_value_duplicate_item(name, path)
            for name, path in zip(names, paths, strict=False)
        ):
            return (
                "low_value_duplicate",
                "low",
                False,
                "Repeated local utility helpers are preserved as low-value duplicate findings.",
            )

        severity = (
            "high"
            if len(unique_paths) >= 3 or len(items) >= 3 or normalized_length >= 160
            else "medium"
        )
        return (
            "high_value_duplicate",
            severity,
            True,
            "Cross-file duplicate logic likely deserves consolidation or extraction review.",
        )

    def _churn_ownership(
        self: AnalysisRunnerProtocol, nodes: list[NodeRecord]
    ) -> dict[str, int]:
        file_paths = self._collect_file_paths(nodes)

        if not file_paths:
            return {"files": 0}

        churn: dict[str, int] = {path: 0 for path in file_paths}
        owners: dict[str, dict[str, int]] = {path: {} for path in file_paths}

        try:
            result = subprocess.run(
                ["git", "log", "--name-only", "--pretty=%H|%an"],
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                current_author = None
                for line in result.stdout.splitlines():
                    if not line.strip():
                        continue
                    if "|" in line:
                        _, author = line.split("|", 1)
                        current_author = author.strip()
                        continue
                    path = line.strip().replace("\\", "/")
                    if path in churn:
                        churn[path] += 1
                        if current_author:
                            owners[path][current_author] = (
                                owners[path].get(current_author, 0) + 1
                            )
        except Exception:
            pass

        top_churn = sorted(churn.items(), key=lambda item: item[1], reverse=True)[:10]
        ownership = {
            path: max(author_counts.items(), key=lambda item: item[1])[0]
            for path, author_counts in owners.items()
            if author_counts
        }

        report_payload = {
            "top_churn": top_churn,
            "ownership": ownership,
        }
        self._write_json_report("churn_report.json", report_payload)

        return {"files": len(file_paths)}

    def _public_api_surface(
        self: AnalysisRunnerProtocol, nodes: list[NodeRecord]
    ) -> dict[str, int]:
        exported_nodes = [
            node for node in nodes if node.properties.get(cs.KEY_IS_EXPORTED)
        ]
        endpoint_nodes = [
            node for node in nodes if cs.NodeLabel.ENDPOINT.value in node.labels
        ]
        entrypoint_nodes = [
            node
            for node in nodes
            if (
                cs.NodeLabel.FUNCTION.value in node.labels
                or cs.NodeLabel.METHOD.value in node.labels
            )
            and self._is_runtime_source_path(
                self._canonical_relative_path(node.properties)
            )
            and bool(node.properties.get(cs.KEY_IS_ENTRY_POINT))
        ]

        public_nodes: list[NodeRecord] = []
        seen_ids: set[int] = set()
        for node in [*exported_nodes, *endpoint_nodes, *entrypoint_nodes]:
            if node.node_id in seen_ids:
                continue
            seen_ids.add(node.node_id)
            public_nodes.append(node)

        symbols = [
            {
                "qualified_name": node.properties.get(cs.KEY_QUALIFIED_NAME),
                "name": node.properties.get(cs.KEY_NAME),
                "path": self._canonical_relative_path(node.properties),
                "labels": node.labels,
            }
            for node in public_nodes
        ]
        report_payload = {
            "summary": {
                "public_symbols": len(public_nodes),
                "exported_symbols": len(exported_nodes),
                "endpoint_symbols": len(endpoint_nodes),
            },
            "reason": (
                "No exported symbols or endpoint nodes detected"
                if not symbols
                else None
            ),
            "symbols": symbols,
        }
        self._write_json_report("public_api_report.json", report_payload)
        return {"public_symbols": len(public_nodes)}

    def _duplicate_code_report(
        self: AnalysisRunnerProtocol,
        nodes: list[NodeRecord],
        module_path_map: dict[str, str],
    ) -> dict[str, int]:
        class DuplicateSymbolItem(TypedDict):
            qualified_name: object
            name: object
            path: str
            start_line: int
            end_line: int
            label: str

        class DuplicateBucket(TypedDict):
            normalized_length: int
            items: list[DuplicateSymbolItem]

        buckets: dict[str, DuplicateBucket] = {}

        for node in nodes:
            if (
                cs.NodeLabel.FUNCTION.value not in node.labels
                and cs.NodeLabel.METHOD.value not in node.labels
            ):
                continue
            path = self._resolve_node_path(node, module_path_map)
            start_line = int(str(node.properties.get(cs.KEY_START_LINE) or 0))
            end_line = int(str(node.properties.get(cs.KEY_END_LINE) or 0))
            if not path or not start_line or not end_line:
                continue
            if self._should_skip_duplicate_path(path):
                continue
            source = extract_source_lines(self.repo_path / path, start_line, end_line)
            if not source:
                continue
            normalized = QualityMixin._normalize_duplicate_source(source)
            if len(normalized) < 40:
                continue
            bucket_key = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            bucket = buckets.setdefault(
                bucket_key,
                {"normalized_length": len(normalized), "items": []},
            )
            bucket["items"].append(
                {
                    "qualified_name": node.properties.get(cs.KEY_QUALIFIED_NAME),
                    "name": node.properties.get(cs.KEY_NAME),
                    "path": path,
                    "start_line": start_line,
                    "end_line": end_line,
                    "label": (
                        cs.NodeLabel.METHOD.value
                        if cs.NodeLabel.METHOD.value in node.labels
                        else cs.NodeLabel.FUNCTION.value
                    ),
                }
            )

        raw_groups = [bucket for bucket in buckets.values() if len(bucket["items"]) > 1]
        actionable_groups: list[dict[str, object]] = []
        ignored_groups: list[dict[str, object]] = []
        category_totals: dict[str, int] = {}

        for bucket in raw_groups:
            items = list(bucket["items"])
            normalized_length = int(bucket["normalized_length"])
            category, severity, actionable, reason = (
                QualityMixin._classify_duplicate_group(
                    items,
                    normalized_length,
                )
            )
            category_totals[category] = category_totals.get(category, 0) + 1
            group_payload = {
                "category": category,
                "severity": severity,
                "actionable": actionable,
                "reason": reason,
                "instance_count": len(items),
                "unique_path_count": len(
                    {str(item.get("path") or "") for item in items}
                ),
                "normalized_length": normalized_length,
                "qualified_names": [item["qualified_name"] for item in items],
                "paths": sorted({str(item.get("path") or "") for item in items}),
                "symbols": sorted(
                    items,
                    key=lambda item: (
                        str(item.get("path") or ""),
                        int(item.get("start_line") or 0),
                    ),
                ),
            }
            if actionable:
                actionable_groups.append(group_payload)
            else:
                ignored_groups.append(group_payload)

        severity_order = {"high": 0, "medium": 1, "low": 2}
        actionable_groups.sort(
            key=lambda item: (
                severity_order.get(str(item["severity"]), 9),
                -int(item["instance_count"]),
                -int(item["normalized_length"]),
            )
        )
        ignored_groups.sort(
            key=lambda item: (
                severity_order.get(str(item["severity"]), 9),
                str(item["category"]),
                -int(item["instance_count"]),
            )
        )

        payload = {
            "summary": {
                "raw_duplicate_groups": len(raw_groups),
                "actionable_groups": len(actionable_groups),
                "ignored_groups": len(ignored_groups),
                "category_totals": category_totals,
            },
            "reason": (
                "No actionable duplicate groups found"
                if not actionable_groups
                else None
            ),
            "duplicate_groups": actionable_groups,
            "ignored_groups": ignored_groups,
        }
        self._write_json_report("duplicate_code_report.json", payload)
        return {
            "duplicate_groups": len(actionable_groups),
            "raw_duplicate_groups": len(raw_groups),
        }

    def _test_coverage_proxy(
        self: AnalysisRunnerProtocol, nodes: list[NodeRecord]
    ) -> dict[str, int]:
        file_nodes = [node for node in nodes if cs.NodeLabel.FILE.value in node.labels]
        file_paths = [
            self._canonical_relative_path(node.properties) for node in file_nodes
        ]
        test_files = [
            path
            for path in file_paths
            if re.search(r"(\\|/)(tests|__tests__)(\\|/)|\.test\.|\.spec\.", path)
        ]
        non_test = [path for path in file_paths if path not in test_files]

        covered = 0
        for path in non_test:
            stem = Path(path).stem
            if any(stem in test_path for test_path in test_files):
                covered += 1

        report_payload = {
            "total_files": len(non_test),
            "test_files": len(test_files),
            "covered_files": covered,
        }
        self._write_json_report("test_coverage_proxy.json", report_payload)

        return report_payload
