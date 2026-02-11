from __future__ import annotations

import re
import subprocess
from pathlib import Path

from codebase_rag.core import constants as cs

from ...utils.source_extraction import extract_source_lines
from ..protocols import AnalysisRunnerProtocol
from ..types import NodeRecord


class QualityMixin:
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
        public_nodes = [
            node
            for node in nodes
            if node.properties.get(cs.KEY_IS_EXPORTED)
            or cs.NodeLabel.ENDPOINT.value in node.labels
        ]
        report_payload = [
            {
                "qualified_name": node.properties.get(cs.KEY_QUALIFIED_NAME),
                "name": node.properties.get(cs.KEY_NAME),
                "path": node.properties.get(cs.KEY_PATH),
                "labels": node.labels,
            }
            for node in public_nodes
        ]
        self._write_json_report("public_api_report.json", report_payload)
        return {"public_symbols": len(public_nodes)}

    def _duplicate_code_report(
        self: AnalysisRunnerProtocol,
        nodes: list[NodeRecord],
        module_path_map: dict[str, str],
    ) -> dict[str, int]:
        buckets: dict[str, list[dict[str, object]]] = {}

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
            source = extract_source_lines(self.repo_path / path, start_line, end_line)
            if not source:
                continue
            normalized = re.sub(r"\s+", "", source)
            normalized = re.sub(r"//.*", "", normalized)
            normalized = re.sub(r"#.*", "", normalized)
            if len(normalized) < 40:
                continue
            bucket_key = str(hash(normalized))
            buckets.setdefault(bucket_key, []).append(
                {
                    "qualified_name": node.properties.get(cs.KEY_QUALIFIED_NAME),
                    "path": path,
                }
            )

        duplicates = [items for items in buckets.values() if len(items) > 1]
        self._write_json_report("duplicate_code_report.json", duplicates)
        return {"duplicate_groups": len(duplicates)}

    def _test_coverage_proxy(
        self: AnalysisRunnerProtocol, nodes: list[NodeRecord]
    ) -> dict[str, int]:
        file_nodes = [node for node in nodes if cs.NodeLabel.FILE.value in node.labels]
        file_paths = [
            str(node.properties.get(cs.KEY_PATH) or "") for node in file_nodes
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
