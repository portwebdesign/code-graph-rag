from __future__ import annotations

import os
import re

from codebase_rag.core import constants as cs

from ..protocols import AnalysisRunnerProtocol
from ..types import NodeRecord


class StaticChecksMixin:
    def _unused_variables(
        self: AnalysisRunnerProtocol,
        nodes: list[NodeRecord],
        file_paths: list[str] | None = None,
    ) -> dict[str, int]:
        file_paths = file_paths or self._collect_file_paths(nodes)
        findings: list[dict[str, object]] = []

        for path in file_paths:
            file_path = self.repo_path / path
            if not file_path.exists() or file_path.stat().st_size > 1_000_000:
                continue
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            suffix = file_path.suffix.lower()
            if suffix in {".js", ".jsx", ".ts", ".tsx"}:
                pattern = r"(?:let|const|var)\s+(\w+)"
            elif suffix in {".py"}:
                pattern = r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*="
            else:
                continue

            for match in re.finditer(pattern, content, re.MULTILINE):
                name = match.group(1)
                usages = len(re.findall(rf"\b{re.escape(name)}\b", content))
                if usages <= 1:
                    findings.append({"path": path, "name": name, "usages": usages})

        self._write_json_report("unused_variables_report.json", findings)
        return {
            "unused_variables": len(findings),
            "files_with_unused": len({item["path"] for item in findings}),
        }

    def _unreachable_code(
        self: AnalysisRunnerProtocol,
        nodes: list[NodeRecord],
        file_paths: list[str] | None = None,
    ) -> dict[str, int]:
        file_paths = file_paths or self._collect_file_paths(nodes)
        findings: list[dict[str, object]] = []

        for path in file_paths:
            file_path = self.repo_path / path
            if not file_path.exists() or file_path.stat().st_size > 1_000_000:
                continue
            try:
                lines = file_path.read_text(
                    encoding="utf-8", errors="ignore"
                ).splitlines()
            except Exception:
                continue

            for idx, line in enumerate(lines[:-1]):
                if re.search(r"\b(return|raise|throw)\b", line):
                    next_line = lines[idx + 1].strip()
                    if next_line and not next_line.startswith(
                        ("}", "elif", "except", "finally")
                    ):
                        findings.append(
                            {"path": path, "line": idx + 2, "code": next_line}
                        )

        self._write_json_report("unreachable_code_report.json", findings)
        return {
            "unreachable_blocks": len(findings),
            "files_with_unreachable": len({item["path"] for item in findings}),
        }

    def _refactoring_candidates(
        self: AnalysisRunnerProtocol, nodes: list[NodeRecord]
    ) -> dict[str, int]:
        threshold = int(os.getenv("CODEGRAPH_REFACTOR_LOC_THRESHOLD", "50"))
        candidates: list[dict[str, object]] = []

        for node in nodes:
            if (
                cs.NodeLabel.FUNCTION.value not in node.labels
                and cs.NodeLabel.METHOD.value not in node.labels
            ):
                continue
            start_line = int(str(node.properties.get(cs.KEY_START_LINE) or 0))
            end_line = int(str(node.properties.get(cs.KEY_END_LINE) or 0))
            if not start_line or not end_line or end_line < start_line:
                continue
            loc = end_line - start_line + 1
            if loc >= threshold:
                candidates.append(
                    {
                        "qualified_name": node.properties.get(cs.KEY_QUALIFIED_NAME),
                        "path": node.properties.get(cs.KEY_PATH),
                        "start_line": start_line,
                        "end_line": end_line,
                        "lines_of_code": loc,
                    }
                )

        self._write_json_report("refactoring_candidates_report.json", candidates)
        return {
            "candidates": len(candidates),
            "threshold": threshold,
        }

    def _secret_scan(
        self: AnalysisRunnerProtocol,
        nodes: list[NodeRecord],
        file_paths: list[str] | None = None,
    ) -> dict[str, int]:
        file_paths = file_paths or self._collect_file_paths(nodes)
        patterns = {
            "aws_access_key": r"AKIA[0-9A-Z]{16}",
            "google_api_key": r"AIza[0-9A-Za-z\-_]{35}",
            "github_token": r"ghp_[0-9A-Za-z]{36,}",
            "slack_token": r"xox[baprs]-[0-9A-Za-z-]{10,48}",
            "private_key": r"-----BEGIN (?:RSA|DSA|EC|OPENSSH) PRIVATE KEY-----",
        }
        findings: list[dict[str, object]] = []

        for path in file_paths:
            file_path = self.repo_path / path
            if not file_path.exists() or file_path.stat().st_size > 1_000_000:
                continue
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            for label, pattern in patterns.items():
                for match in re.finditer(pattern, content):
                    line_number = content.count("\n", 0, match.start()) + 1
                    findings.append(
                        {
                            "path": path,
                            "type": label,
                            "line": line_number,
                        }
                    )

        self._write_json_report("secret_scan_report.json", findings)
        return {
            "findings": len(findings),
            "files_with_findings": len({item["path"] for item in findings}),
        }
