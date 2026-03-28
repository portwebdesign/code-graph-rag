from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from codebase_rag.core import constants as cs
from codebase_rag.services.protocols import QueryProtocol

from .base_module import AnalysisContext, AnalysisModule


class ApiComplianceModule(AnalysisModule):
    _SKIP_DIRS = {
        ".git",
        ".idea",
        ".next",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "generated",
        "node_modules",
        "site-packages",
        "vendor",
        "venv",
    }
    _SKIP_PATH_MARKERS = {
        "spec",
        "tests",
        "test",
        "testdata",
        "__tests__",
    }

    def get_name(self) -> str:
        return "api_compliance"

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        endpoints = self._fetch_graph_endpoints(context)
        ignored_paths: set[str] = set()
        source_mode = "graph"

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
            source_mode = "source_scan"

        filtered_endpoints: list[dict[str, str]] = []
        seen_endpoint_keys: set[tuple[str, str, str]] = set()
        for endpoint in endpoints:
            file_path = str(endpoint.get("file", "")).replace("\\", "/")
            if self._should_ignore_path(file_path):
                if file_path:
                    ignored_paths.add(file_path)
                continue
            if not self._is_graph_analysis_endpoint(endpoint):
                continue
            endpoint_key = (
                str(endpoint.get("method", "")).strip().upper(),
                str(endpoint.get("path", "")).strip(),
                file_path,
            )
            if endpoint_key in seen_endpoint_keys:
                continue
            seen_endpoint_keys.add(endpoint_key)
            normalized = dict(endpoint)
            normalized["file"] = file_path
            filtered_endpoints.append(normalized)
        endpoints = filtered_endpoints

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
            "summary": {
                "endpoints": len(endpoints),
                "violations": len(violations),
                "ignored_paths": len(ignored_paths),
                "source_mode": source_mode,
            },
            "endpoints": endpoints,
            "violations": violations,
            "reason_counts": reason_counts,
            "ignored_paths": sorted(ignored_paths),
            "confidence": 0.92 if source_mode == "graph" else 0.76,
            "next_actions": [
                "architecture_bundle",
                "multi_hop_analysis",
                "risk_bundle" if violations else "query_code_graph",
            ],
        }
        context.runner._write_json_report("api_compliance_report.json", report)

        return {
            "endpoints": len(endpoints),
            "violations": len(violations),
            "reason_counts": reason_counts,
            "top_violations": violations[:50],
        }

    @staticmethod
    def _path_depth(path: str) -> int:
        return len([segment for segment in str(path or "").split("/") if segment])

    @classmethod
    def _endpoint_group_key(cls, endpoint: dict[str, object]) -> tuple[object, ...]:
        method = str(endpoint.get("method", "")).strip().upper()
        file_path = str(endpoint.get("file", "")).replace("\\", "/").strip()
        local_route_path = str(
            endpoint.get("local_route_path") or endpoint.get("path") or ""
        ).strip()
        handler_qns = tuple(
            sorted(
                {
                    str(qn).strip()
                    for qn in cast(list[object], endpoint.get("handler_qns", []))
                    if str(qn).strip()
                }
            )
        )
        if handler_qns:
            return (method, file_path, local_route_path, handler_qns)
        return (method, file_path, local_route_path)

    @classmethod
    def _endpoint_preference(
        cls, endpoint: dict[str, object]
    ) -> tuple[int, int, int, int]:
        expose_count = int(cast(Any, endpoint.get("expose_count", 0)) or 0)
        prefix_count = int(cast(Any, endpoint.get("prefix_count", 0)) or 0)
        path = str(endpoint.get("path", "")).strip()
        return (
            1 if expose_count > 0 else 0,
            1 if prefix_count > 0 else 0,
            cls._path_depth(path),
            len(path),
        )

    @classmethod
    def _normalize_graph_endpoints(
        cls,
        endpoints: list[dict[str, object]],
        *,
        module_paths: list[str] | None = None,
    ) -> list[dict[str, str]]:
        grouped: dict[tuple[object, ...], dict[str, object]] = {}
        module_path_set = {path.replace("\\", "/") for path in module_paths or []}

        for raw_endpoint in endpoints:
            path = str(raw_endpoint.get("path", "")).strip()
            if not path:
                continue
            file_path = str(raw_endpoint.get("file", "")).replace("\\", "/").strip()
            exposed_module_paths = [
                str(path).replace("\\", "/").strip()
                for path in cast(
                    list[object], raw_endpoint.get("exposed_module_paths", [])
                )
                if str(path).strip()
            ]
            prefix_module_paths = [
                str(path).replace("\\", "/").strip()
                for path in cast(
                    list[object], raw_endpoint.get("prefix_module_paths", [])
                )
                if str(path).strip()
            ]
            if module_path_set and not (
                file_path in module_path_set
                or any(path in module_path_set for path in exposed_module_paths)
                or any(path in module_path_set for path in prefix_module_paths)
            ):
                continue

            normalized = dict(raw_endpoint)
            normalized["file"] = file_path
            normalized["method"] = (
                str(raw_endpoint.get("method", "REQUEST")).strip().upper()
            )
            normalized["path"] = path
            normalized["framework"] = str(raw_endpoint.get("framework", "")).strip()
            normalized["handler_qns"] = [
                str(qn).strip()
                for qn in cast(list[object], raw_endpoint.get("handler_qns", []))
                if str(qn).strip()
            ]
            normalized["exposed_module_paths"] = exposed_module_paths
            normalized["prefix_module_paths"] = prefix_module_paths
            expose_count = int(cast(Any, raw_endpoint.get("expose_count", 0)) or 0)
            prefix_count = int(cast(Any, raw_endpoint.get("prefix_count", 0)) or 0)
            normalized["expose_count"] = expose_count
            normalized["prefix_count"] = prefix_count
            normalized["canonical_route_layer"] = (
                "relation_propagated"
                if expose_count > 0 or prefix_count > 0
                else "direct_endpoint_node"
            )

            key = cls._endpoint_group_key(normalized)
            current = grouped.get(key)
            if current is None or cls._endpoint_preference(
                normalized
            ) > cls._endpoint_preference(current):
                grouped[key] = normalized

        normalized_endpoints: list[dict[str, str]] = []
        seen_endpoint_keys: set[tuple[str, str, str]] = set()
        for endpoint in grouped.values():
            endpoint_key = (
                str(endpoint.get("method", "")).strip().upper(),
                str(endpoint.get("path", "")).strip(),
                str(endpoint.get("file", "")).replace("\\", "/").strip(),
            )
            if endpoint_key in seen_endpoint_keys:
                continue
            seen_endpoint_keys.add(endpoint_key)
            normalized_endpoints.append(cast(dict[str, str], endpoint))

        normalized_endpoints.sort(
            key=lambda item: (
                str(item.get("path", "")),
                str(item.get("method", "")),
                str(item.get("file", "")),
            )
        )
        return normalized_endpoints

    @staticmethod
    def _iter_files(repo_path: Path, module_paths: list[str] | None) -> list[Path]:
        if module_paths:
            resolved_paths: list[Path] = []
            for path in module_paths:
                if not path:
                    continue
                candidate = repo_path / path
                if not candidate.exists() or not candidate.is_file():
                    continue
                if ApiComplianceModule._should_ignore_path(
                    candidate.relative_to(repo_path).as_posix()
                ):
                    continue
                resolved_paths.append(candidate)
            return resolved_paths

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
            if len(collected) >= 500:
                break
            if not path.is_file():
                continue
            try:
                relative = path.relative_to(repo_path).as_posix()
            except ValueError:
                continue
            if ApiComplianceModule._should_ignore_path(relative):
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
            r"\b(?:app|router|group|engine|mux|r)\.(GET|POST|PUT|DELETE|PATCH|Any)\s*\(\s*['\"]([^'\"]+)['\"]"
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

        deduped: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for endpoint in results:
            endpoint_key = (
                str(endpoint.get("method", "")).strip().upper(),
                str(endpoint.get("path", "")).strip(),
                str(endpoint.get("file", "")).replace("\\", "/").strip(),
            )
            if endpoint_key in seen:
                continue
            seen.add(endpoint_key)
            normalized = dict(endpoint)
            normalized["file"] = endpoint_key[2]
            deduped.append(normalized)
        return deduped

    @classmethod
    def _should_ignore_path(cls, path: str) -> bool:
        normalized = str(path or "").replace("\\", "/").strip().lower()
        if not normalized:
            return False
        parts = set(normalized.split("/"))
        return not cls._SKIP_DIRS.isdisjoint(
            parts
        ) or not cls._SKIP_PATH_MARKERS.isdisjoint(parts)

    @staticmethod
    def _is_graph_analysis_endpoint(endpoint: Mapping[str, object]) -> bool:
        framework = str(endpoint.get("framework") or "").strip().lower()
        if framework in {"http", "graphql"}:
            return False
        return True

    @staticmethod
    def _fetch_graph_endpoints(context: AnalysisContext) -> list[dict[str, str]]:
        if not hasattr(context.runner.ingestor, "fetch_all"):
            return []

        ingestor = cast(QueryProtocol, context.runner.ingestor)
        query = """
        MATCH (e:Endpoint {project_name: $project_name})
        WHERE coalesce(e.route_path, '') <> ''
        OPTIONAL MATCH (handler)-[:HAS_ENDPOINT]->(e)
        OPTIONAL MATCH (e)-[:ROUTES_TO_ACTION]->(action)
        OPTIONAL MATCH (module)-[ex:EXPOSES_ENDPOINT]->(e)
        OPTIONAL MATCH (prefixModule)-[pref:PREFIXES_ENDPOINT]->(e)
        RETURN
          coalesce(e.qualified_name, '') AS qualified_name,
          coalesce(e.http_method, 'REQUEST') AS method,
          e.route_path AS path,
          coalesce(e.local_route_path, '') AS local_route_path,
          coalesce(e.path, '') AS file,
          coalesce(e.framework, '') AS framework,
          [qn IN collect(DISTINCT coalesce(handler.qualified_name, '')) WHERE qn <> ''] +
            [qn IN collect(DISTINCT coalesce(action.qualified_name, '')) WHERE qn <> ''] AS handler_qns,
          [path IN collect(DISTINCT coalesce(module.path, '')) WHERE path <> ''] AS exposed_module_paths,
          [path IN collect(DISTINCT coalesce(prefixModule.path, '')) WHERE path <> ''] AS prefix_module_paths,
          count(DISTINCT ex) AS expose_count,
          count(DISTINCT pref) AS prefix_count
        """

        try:
            rows = ingestor.fetch_all(
                query, {cs.KEY_PROJECT_NAME: context.runner.project_name}
            )
        except Exception:
            return []

        raw_endpoints = [
            cast(dict[str, object], row) for row in rows if isinstance(row, dict)
        ]
        normalized = ApiComplianceModule._normalize_graph_endpoints(
            raw_endpoints,
            module_paths=context.module_paths,
        )
        return [
            endpoint
            for endpoint in normalized
            if not ApiComplianceModule._should_ignore_path(
                str(endpoint.get("file", ""))
            )
            and int(endpoint.get("expose_count", 0) or 0)
            + int(endpoint.get("prefix_count", 0) or 0)
            + len(cast(list[object], endpoint.get("handler_qns", [])))
            > 0
        ]

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
