from __future__ import annotations

import keyword
import os
import re

from codebase_rag.core import constants as cs

from ..protocols import AnalysisRunnerProtocol
from ..types import NodeRecord


class StaticChecksMixin:
    @staticmethod
    def _should_skip_static_analysis_path(path: str) -> bool:
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
        if any(part in skip_parts for part in parts):
            return True
        return normalized.endswith((".min.js", ".min.css"))

    @staticmethod
    def _is_likely_ignored_variable(name: str) -> bool:
        if not name:
            return True
        if name.startswith("_"):
            return True
        if name.isupper():
            return True
        if keyword.iskeyword(name):
            return True
        return False

    @staticmethod
    def _leading_indent(line: str) -> int:
        return len(line) - len(line.lstrip(" \t"))

    @staticmethod
    def _next_executable_line(
        lines: list[str], start_idx: int
    ) -> tuple[int | None, str | None]:
        for idx in range(start_idx + 1, len(lines)):
            candidate = lines[idx].strip()
            if not candidate:
                continue
            if candidate.startswith(("#", "//", "/*", "*", "*/")):
                continue
            return idx, lines[idx]
        return None, None

    @staticmethod
    def _is_control_transfer_line(line: str) -> bool:
        if not re.search(r"\b(return|raise|throw|break|continue)\b", line):
            return False
        if re.search(r"\bif\b.*\b(return|raise|throw|break|continue)\b", line):
            return False
        if re.search(r"\?.*:", line):
            return False
        return True

    def _unused_variables(
        self: AnalysisRunnerProtocol,
        nodes: list[NodeRecord],
        file_paths: list[str] | None = None,
    ) -> dict[str, int]:
        file_paths = file_paths or self._collect_file_paths(nodes)
        findings: list[dict[str, object]] = []

        for path in file_paths:
            if self._should_skip_static_analysis_path(path):
                continue
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
                if self._is_likely_ignored_variable(name):
                    continue
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
            if self._should_skip_static_analysis_path(path):
                continue
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
                if not self._is_control_transfer_line(line):
                    continue
                next_idx, next_line_raw = self._next_executable_line(lines, idx)
                if next_idx is None or next_line_raw is None:
                    continue
                next_line = next_line_raw.strip()
                if next_line.startswith(
                    ("}", "elif", "except", "finally", "case ", "default:")
                ):
                    continue

                current_indent = self._leading_indent(line)
                next_indent = self._leading_indent(next_line_raw)
                if next_indent < current_indent:
                    continue

                findings.append(
                    {
                        "path": path,
                        "line": next_idx + 1,
                        "code": next_line,
                    }
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
