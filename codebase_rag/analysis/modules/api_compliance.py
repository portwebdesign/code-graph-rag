from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

from codebase_rag.core import constants as cs
from codebase_rag.services.protocols import QueryProtocol

from .base_module import AnalysisContext, AnalysisModule


class ApiComplianceModule(AnalysisModule):
    def get_name(self) -> str:
        return "api_compliance"

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        endpoints = self._fetch_graph_endpoints(context)

        if not endpoints:
            repo_path = context.runner.repo_path
            files = self._iter_files(repo_path, context.module_paths)
            for file_path in files:
                try:
                    source = file_path.read_text(
                        encoding=cs.ENCODING_UTF8,
                        errors="ignore",
                    )
                except Exception:
                    continue
                endpoints.extend(self._extract_endpoints(source, file_path))

        violations: list[dict[str, object]] = []
        for endpoint in endpoints:
            reasons = self._violation_reasons(endpoint["path"])
            if reasons:
                violations.append({**endpoint, "reasons": reasons})

        reason_counts: dict[str, int] = {}
        for violation in violations:
            for reason in cast(list[str], violation.get("reasons", [])):
                reason_counts[reason] = reason_counts.get(reason, 0) + 1

        report = {
            "endpoints": endpoints,
            "violations": violations,
            "reason_counts": reason_counts,
        }
        context.runner._write_json_report("api_compliance_report.json", report)

        return {
            "endpoints": len(endpoints),
            "violations": len(violations),
            "reason_counts": reason_counts,
            "top_violations": violations[:50],
        }

    @staticmethod
    def _iter_files(repo_path: Path, module_paths: list[str] | None) -> list[Path]:
        if module_paths:
            return [repo_path / path for path in module_paths if path]

        extensions = {
            *cs.PY_EXTENSIONS,
            *cs.JS_EXTENSIONS,
            *cs.TS_EXTENSIONS,
            *cs.JAVA_EXTENSIONS,
            *cs.KOTLIN_EXTENSIONS,
            *cs.PHP_EXTENSIONS,
            *cs.CS_EXTENSIONS,
            *cs.GO_EXTENSIONS,
            *cs.RUBY_EXTENSIONS,
        }
        collected: list[Path] = []
        for path in repo_path.rglob("*"):
            if len(collected) >= 300:
                break
            if not path.is_file():
                continue
            if path.suffix.lower() not in extensions:
                continue
            collected.append(path)
        return collected

    @staticmethod
    def _extract_endpoints(source: str, file_path: Path) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        py_pattern = re.compile(
            r"@(?:app|router|bp)\.(get|post|put|delete|patch|api_route)\(\s*['\"]([^'\"]+)['\"]",
            re.IGNORECASE,
        )
        flask_pattern = re.compile(
            r"@(?:app|bp)\.route\(\s*['\"]([^'\"]+)['\"](?:\s*,\s*methods=\[([^\]]+)\])?",
            re.IGNORECASE,
        )
        js_pattern = re.compile(
            r"(?:app|router)\.(get|post|put|delete|patch|all)\(\s*['\"]([^'\"]+)['\"]",
            re.IGNORECASE,
        )
        spring_pattern = re.compile(
            r"@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)\(\s*\"([^\"]+)\"",
            re.IGNORECASE,
        )
        csharp_map_pattern = re.compile(
            r"\.Map(Get|Post|Put|Delete|Patch|Methods)\s*\(\s*['\"]([^'\"]+)['\"]",
            re.IGNORECASE,
        )
        csharp_attr_pattern = re.compile(
            r"\[Http(Get|Post|Put|Delete|Patch)(?:\(\s*['\"]([^'\"]*)['\"]\s*\))?\]",
            re.IGNORECASE,
        )
        rails_pattern = re.compile(
            r"\b(get|post|put|patch|delete)\s+['\"]([^'\"]+)['\"]",
            re.IGNORECASE,
        )
        go_web_pattern = re.compile(
            r"\.(GET|POST|PUT|DELETE|PATCH|Any)\s*\(\s*['\"]([^'\"]+)['\"]",
            re.IGNORECASE,
        )
        nest_pattern = re.compile(
            r"@(Get|Post|Put|Delete|Patch)\s*\(\s*['\"]([^'\"]*)['\"]\s*\)",
            re.IGNORECASE,
        )
        laravel_pattern = re.compile(
            r"Route::(get|post|put|patch|delete|options|any)\s*\(\s*['\"]([^'\"]+)['\"]",
            re.IGNORECASE,
        )

        for match in py_pattern.finditer(source):
            method = match.group(1).upper()
            path = match.group(2)
            results.append({"method": method, "path": path, "file": str(file_path)})

        for match in flask_pattern.finditer(source):
            path = match.group(1)
            methods = match.group(2) or "GET"
            results.append({"method": methods, "path": path, "file": str(file_path)})

        for match in js_pattern.finditer(source):
            method = match.group(1).upper()
            path = match.group(2)
            results.append({"method": method, "path": path, "file": str(file_path)})

        for match in spring_pattern.finditer(source):
            annotation = match.group(1).lower()
            path = match.group(2)
            method = ApiComplianceModule._spring_annotation_to_method(annotation)
            results.append({"method": method, "path": path, "file": str(file_path)})

        for match in csharp_map_pattern.finditer(source):
            method = match.group(1).upper()
            if method == "METHODS":
                method = "MULTI"
            path = match.group(2)
            results.append({"method": method, "path": path, "file": str(file_path)})

        for match in csharp_attr_pattern.finditer(source):
            method = match.group(1).upper()
            path = match.group(2) or ""
            results.append({"method": method, "path": path, "file": str(file_path)})

        for match in rails_pattern.finditer(source):
            method = match.group(1).upper()
            path = match.group(2)
            results.append({"method": method, "path": path, "file": str(file_path)})

        for match in go_web_pattern.finditer(source):
            method = match.group(1).upper()
            if method == "ANY":
                method = "MULTI"
            path = match.group(2)
            results.append({"method": method, "path": path, "file": str(file_path)})

        for match in nest_pattern.finditer(source):
            method = match.group(1).upper()
            path = match.group(2) or ""
            results.append({"method": method, "path": path, "file": str(file_path)})

        for match in laravel_pattern.finditer(source):
            method = match.group(1).upper()
            path = match.group(2)
            results.append({"method": method, "path": path, "file": str(file_path)})

        return results

    @staticmethod
    def _fetch_graph_endpoints(context: AnalysisContext) -> list[dict[str, str]]:
        if not hasattr(context.runner.ingestor, "fetch_all"):
            return []

        ingestor = cast(QueryProtocol, context.runner.ingestor)
        query = """
        MATCH (e:Endpoint)
        WHERE e.route_path IS NOT NULL
        RETURN
          coalesce(e.http_method, 'REQUEST') AS method,
          e.route_path AS path,
          coalesce(e.path, '') AS file,
          coalesce(e.framework, '') AS framework
        """

        try:
            rows = ingestor.fetch_all(query, {})
        except Exception:
            return []

        module_paths = set(context.module_paths or [])
        endpoints: list[dict[str, str]] = []
        for row in rows:
            file_path = str(row.get("file", ""))
            if module_paths and file_path and file_path not in module_paths:
                continue
            endpoints.append(
                {
                    "method": str(row.get("method", "REQUEST")),
                    "path": str(row.get("path", "")),
                    "file": file_path,
                    "framework": str(row.get("framework", "")),
                }
            )
        return endpoints

    @staticmethod
    def _spring_annotation_to_method(annotation: str) -> str:
        mapping = {
            "getmapping": "GET",
            "postmapping": "POST",
            "putmapping": "PUT",
            "deletemapping": "DELETE",
            "patchmapping": "PATCH",
        }
        return mapping.get(annotation, "REQUEST")

    @staticmethod
    def _violation_reasons(path: str) -> list[str]:
        reasons: list[str] = []
        verbs = ("get", "create", "update", "delete", "set", "add", "remove", "fetch")
        segments = [segment for segment in path.strip("/").split("/") if segment]
        for segment in segments:
            lowered = segment.lower()
            if any(lowered.startswith(verb) for verb in verbs):
                reasons.append("verb_in_path")
            if segment in ("get", "post", "put", "delete", "patch"):
                reasons.append("method_as_segment")
            if "_" in segment:
                reasons.append("underscore_in_path")
            if any(char.isupper() for char in segment):
                reasons.append("camel_case_segment")
        if "?" in path:
            reasons.append("query_in_path")
        return list(dict.fromkeys(reasons))
