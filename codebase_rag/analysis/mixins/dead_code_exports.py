from __future__ import annotations

import ast
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from ...core import constants as cs
from ..protocols import AnalysisRunnerProtocol


class DeadCodeExportsMixin:
    RUNTIME_SOURCE_EXTENSIONS: set[str] = {
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".java",
        ".kt",
        ".kts",
        ".go",
        ".rs",
        ".rb",
        ".php",
        ".cs",
        ".cpp",
        ".cc",
        ".cxx",
        ".c",
        ".h",
        ".hpp",
        ".hh",
        ".swift",
        ".scala",
        ".lua",
    }

    RISK_WEIGHTS: dict[str, int] = {
        "candidate_dead_code": 3,
        "parser_or_language_tooling": 2,
        "infrastructure_helper": 2,
        "analysis_tooling": 1,
        "cli_or_entrypoint": 1,
        "framework_registered": 0,
        "dynamic_or_magic": 0,
    }

    NON_RUNTIME_BUILD_CONFIG_FILENAMES: set[str] = {
        "babel.config.js",
        "babel.config.cjs",
        "babel.config.mjs",
        "babel.config.ts",
        "esbuild.config.js",
        "esbuild.config.cjs",
        "esbuild.config.mjs",
        "esbuild.config.ts",
        "postcss.config.js",
        "postcss.config.cjs",
        "postcss.config.mjs",
        "postcss.config.ts",
        "rollup.config.js",
        "rollup.config.cjs",
        "rollup.config.mjs",
        "rollup.config.ts",
        "rspack.config.js",
        "rspack.config.cjs",
        "rspack.config.mjs",
        "rspack.config.ts",
        "tailwind.config.js",
        "tailwind.config.cjs",
        "tailwind.config.mjs",
        "tailwind.config.ts",
        "tsup.config.js",
        "tsup.config.cjs",
        "tsup.config.mjs",
        "tsup.config.ts",
        "vite.config.js",
        "vite.config.cjs",
        "vite.config.mjs",
        "vite.config.ts",
        "vite.config.cts",
        "vite.config.mts",
        "vitest.config.js",
        "vitest.config.cjs",
        "vitest.config.mjs",
        "vitest.config.ts",
        "vitest.config.cts",
        "vitest.config.mts",
        "webpack.config.js",
        "webpack.config.cjs",
        "webpack.config.mjs",
        "webpack.config.ts",
    }

    @staticmethod
    def _normalize_dead_code_path(path: str | None) -> str:
        return str(path or "").replace("\\", "/").strip()

    @staticmethod
    def _is_test_dead_code_item(path: str, name: str) -> bool:
        normalized = path.lower()
        lowered_name = name.lower()
        if re.search(r"(^|/)(test|tests|__tests__)(/|$)", normalized):
            return True
        if lowered_name.startswith("test_"):
            return True
        return bool(re.search(r"\.test\.|\.spec\.", normalized))

    @staticmethod
    def _is_generated_or_noise_path(path: str) -> bool:
        normalized = path.lower().strip("/")
        parts = [part for part in normalized.split("/") if part]
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
            ".git",
            ".github",
            ".vscode",
            ".idea",
        }
        return any(part in skip_parts for part in parts)

    @classmethod
    def _is_runtime_source_path(cls, path: str) -> bool:
        normalized = path.replace("\\", "/").strip()
        if not normalized:
            return False

        suffix = Path(normalized).suffix.lower()
        if suffix not in cls.RUNTIME_SOURCE_EXTENSIONS:
            return False

        filename = Path(normalized).name.lower()
        config_filenames = {
            "pyproject.toml",
            "poetry.lock",
            "package.json",
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
            "tsconfig.json",
            "jsconfig.json",
            "settings.json",
            "launch.json",
            "tasks.json",
            "docker-compose.yaml",
            "docker-compose.yml",
            "qdrant_config.yaml",
            "qdrant_config.yml",
        }
        if filename in config_filenames:
            return False
        if filename in cls.NON_RUNTIME_BUILD_CONFIG_FILENAMES:
            return False

        return True

    @staticmethod
    def _is_anonymous_callback_symbol(name: str) -> bool:
        return (
            name.startswith("anonymous_")
            or name.startswith(cs.IIFE_ARROW_PREFIX)
            or name.startswith(cs.IIFE_FUNC_PREFIX)
        )

    def _is_python_package_reexport(
        self: AnalysisRunnerProtocol, path: str, name: str
    ) -> bool:
        normalized = self._normalize_dead_code_path(path)
        module_path = Path(normalized)
        if module_path.suffix.lower() != ".py":
            return False
        if module_path.name in {"__init__.py", "__main__.py"}:
            return False

        package_init_path = module_path.with_name("__init__.py")
        init_source = self._read_dead_code_source_text(
            str(package_init_path).replace("\\", "/")
        )
        if not init_source:
            return False

        import_pattern = re.compile(
            rf"^\s*from\s+\.{re.escape(module_path.stem)}\s+import\s+.*\b{re.escape(name)}\b",
            re.MULTILINE,
        )
        if import_pattern.search(init_source):
            return True

        all_pattern = re.compile(
            rf"__all__\s*=\s*\[[^\]]*['\"]{re.escape(name)}['\"]",
            re.MULTILINE | re.DOTALL,
        )
        return bool(all_pattern.search(init_source))

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value)
        except Exception:
            return 0

    @staticmethod
    def _decorator_signals(item: dict[str, Any]) -> list[str]:
        raw = item.get("decorators")
        if not isinstance(raw, list):
            return []
        return [str(dec).lower() for dec in raw if str(dec).strip()]

    def _graph_confidence(self, item: dict[str, Any]) -> dict[str, int]:
        call_in_degree = self._safe_int(item.get("call_in_degree") or 0)
        dispatch_in_degree = self._safe_int(item.get("dispatch_in_degree") or 0)
        combined_in_degree = self._safe_int(
            item.get("combined_in_degree") or (call_in_degree + dispatch_in_degree)
        )
        return {
            "call_in_degree": call_in_degree,
            "dispatch_in_degree": dispatch_in_degree,
            "combined_in_degree": combined_in_degree,
            "semantic_registration_links": self._safe_int(
                item.get("semantic_registration_links") or 0
            ),
            "import_in_degree": self._safe_int(item.get("config_reference_links") or 0),
            "decorator_links": self._safe_int(item.get("decorator_links") or 0),
            "registration_links": self._safe_int(item.get("registration_links") or 0),
            "imported_by_cli_links": self._safe_int(
                item.get("imported_by_cli_links") or 0
            ),
            "config_reference_links": self._safe_int(
                item.get("config_reference_links") or 0
            ),
        }

    def _is_framework_registered(
        self, item: dict[str, Any], graph_confidence: dict[str, int]
    ) -> bool:
        decorators = self._decorator_signals(item)
        if graph_confidence["decorator_links"] > 0:
            return True
        if graph_confidence["registration_links"] > 0:
            return True
        if graph_confidence["semantic_registration_links"] > 0:
            return True
        if graph_confidence["imported_by_cli_links"] > 0:
            return True
        if graph_confidence["config_reference_links"] > 0:
            return True
        decorator_tokens = (
            "command",
            "route",
            "controller",
            "injectable",
            "public",
            "mcp",
            "tool",
            "hook",
        )
        return any(
            any(token in dec for token in decorator_tokens) for dec in decorators
        )

    def _dead_code_category(
        self, item: dict[str, Any], graph_confidence: dict[str, int]
    ) -> str:
        path = str(item.get("path") or "").lower()
        name = str(item.get("name") or "")
        qn = str(item.get("qualified_name") or "").lower()

        if self._is_framework_registered(item, graph_confidence):
            return "framework_registered"
        if name.startswith("__") and name.endswith("__"):
            return "dynamic_or_magic"
        if name in {"__getattr__", "__dir__"}:
            return "dynamic_or_magic"
        if (
            path.endswith("/core/cli.py")
            or ".core.cli." in qn
            or name.endswith("_command")
            or name in {"main", "run", "start"}
        ):
            return "cli_or_entrypoint"
        if "/parsers/" in path or "/tools/language.py" in path:
            return "parser_or_language_tooling"
        if "/analysis/" in path:
            return "analysis_tooling"
        if "/infrastructure/" in path or "/utils/" in path:
            return "infrastructure_helper"
        return "candidate_dead_code"

    def _risk_score(self, category: str) -> int:
        return self.RISK_WEIGHTS.get(category, 2)

    @staticmethod
    def _truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y"}
        return bool(value)

    def _reachability_state(
        self,
        item: dict[str, Any],
        graph_confidence: dict[str, int],
    ) -> tuple[bool, str]:
        if graph_confidence["combined_in_degree"] > 0:
            if graph_confidence["dispatch_in_degree"] > 0:
                return True, "dispatch_reference"
            return True, "call_reference"
        if self._truthy(item.get("is_entrypoint_name")):
            return True, "entrypoint"
        if self._truthy(item.get("has_entry_decorator")):
            return True, "decorator"
        if graph_confidence["decorator_links"] > 0:
            return True, "decorator"
        if graph_confidence["registration_links"] > 0:
            return True, "framework_registration"
        if graph_confidence["semantic_registration_links"] > 0:
            return True, "semantic_framework_registration"
        if graph_confidence["imported_by_cli_links"] > 0:
            return True, "cli_reference"
        if graph_confidence["config_reference_links"] > 0:
            return True, "config_reference"
        if self._truthy(item.get("is_exported")):
            return True, "exported"
        return False, "unreachable"

    def _dead_code_score(
        self,
        item: dict[str, Any],
        graph_confidence: dict[str, int],
        is_reachable: bool,
    ) -> int:
        score = 0
        if graph_confidence["combined_in_degree"] == 0:
            score += 50
        else:
            score -= min(70, graph_confidence["combined_in_degree"] * 35)
        if not self._truthy(item.get("is_exported")):
            score += 20
        if not self._truthy(item.get("is_entrypoint_name")):
            score += 20
        if graph_confidence["decorator_links"] == 0 and not self._truthy(
            item.get("has_entry_decorator")
        ):
            score += 10
        score -= min(60, graph_confidence["registration_links"] * 20)
        score -= min(40, graph_confidence["semantic_registration_links"] * 20)
        score -= min(20, graph_confidence["imported_by_cli_links"] * 10)
        score -= min(20, graph_confidence["config_reference_links"] * 10)
        if is_reachable:
            score = min(score, 40)
        return max(0, min(100, score))

    def _apply_dead_code_node_cache(
        self: AnalysisRunnerProtocol,
        dead_functions: list[dict[str, Any]],
        analysis_run_id: str | None = None,
    ) -> dict[str, int]:
        run_id = analysis_run_id or datetime.now(UTC).replace(microsecond=0).isoformat()
        updated = 0
        for item in dead_functions:
            qn = str(item.get("qualified_name") or "").strip()
            label = str(item.get("label") or "").strip() or "Function"
            if not qn or label not in {"Function", "Method"}:
                continue
            graph_confidence = self._graph_confidence(item)
            is_reachable, reachability_source = self._reachability_state(
                item, graph_confidence
            )
            dead_code_score = self._dead_code_score(
                item,
                graph_confidence,
                is_reachable,
            )
            self.ingestor.ensure_node_batch(
                label,
                {
                    "qualified_name": qn,
                    "in_call_count": graph_confidence["call_in_degree"],
                    "in_dispatch_count": graph_confidence["dispatch_in_degree"],
                    "combined_in_count": graph_confidence["combined_in_degree"],
                    "out_call_count": self._safe_int(item.get("out_call_count") or 0),
                    "is_reachable": is_reachable,
                    "reachability_source": reachability_source,
                    "analysis_run_id": run_id,
                    "dead_code_score": dead_code_score,
                },
            )
            updated += 1
        return {"updated_nodes": updated}

    @staticmethod
    def _language_from_path(path: str) -> str:
        suffix = Path(path).suffix.lower().lstrip(".")
        return suffix or "unknown"

    def _write_dead_code_except_test_report(
        self: AnalysisRunnerProtocol,
        dead_functions: list[dict[str, Any]],
        *,
        max_files: int = 200,
        raw_total_dead_symbols: int | None = None,
        suppression_reason_counts: dict[str, int] | None = None,
        suppressed_dead_symbols: int | None = None,
    ) -> dict[str, Any]:
        filtered_items: list[dict[str, Any]] = []
        category_totals: dict[str, int] = {}

        for item in dead_functions:
            path = self._normalize_dead_code_path(str(item.get("path") or ""))
            name = str(item.get("name") or "")
            if not path:
                continue
            if not self._is_runtime_source_path(path):
                continue
            if self._is_generated_or_noise_path(path):
                continue
            if self._is_test_dead_code_item(path, name):
                continue

            graph_confidence = self._graph_confidence(item)
            is_reachable, reachability_source = self._reachability_state(
                item,
                graph_confidence,
            )
            dead_code_score = self._dead_code_score(
                item,
                graph_confidence,
                is_reachable,
            )
            category = self._dead_code_category(item, graph_confidence)
            risk_score = self._risk_score(category)
            category_totals[category] = category_totals.get(category, 0) + 1
            filtered_items.append(
                {
                    "qualified_name": item.get("qualified_name"),
                    "name": name,
                    "path": path,
                    "start_line": item.get("start_line"),
                    "category": category,
                    "risk_score": risk_score,
                    "dead_code_score": dead_code_score,
                    "is_reachable": is_reachable,
                    "reachability_source": reachability_source,
                    "graph_confidence": graph_confidence,
                }
            )

        file_map: dict[str, dict[str, Any]] = {}
        for item in filtered_items:
            path = str(item["path"])
            file_entry = file_map.setdefault(
                path,
                {
                    "path": path,
                    "language": self._language_from_path(path),
                    "dead_symbols_count": 0,
                    "risk_score_sum": 0,
                    "max_risk_score": 0,
                    "categories": {},
                    "dead_symbols": [],
                },
            )
            file_entry["dead_symbols_count"] += 1
            file_entry["risk_score_sum"] += self._safe_int(item.get("risk_score") or 0)
            file_entry["max_risk_score"] = max(
                self._safe_int(file_entry.get("max_risk_score") or 0),
                self._safe_int(item.get("risk_score") or 0),
            )
            category = str(item["category"])
            file_entry["categories"][category] = (
                file_entry["categories"].get(category, 0) + 1
            )
            file_entry["dead_symbols"].append(item)

        for entry in file_map.values():
            entry["dead_symbols"] = sorted(
                entry["dead_symbols"],
                key=lambda symbol: int(str(symbol.get("start_line") or 0)),
            )

        sorted_files = sorted(
            file_map.values(),
            key=lambda entry: (
                -self._safe_int(entry["max_risk_score"]),
                -self._safe_int(entry["risk_score_sum"]),
                -self._safe_int(entry["dead_symbols_count"]),
                str(entry["path"]),
            ),
        )
        selected_files = sorted_files[:max_files]

        high_risk_files = [
            {
                "path": entry["path"],
                "language": entry["language"],
                "dead_symbols_count": entry["dead_symbols_count"],
                "max_risk_score": entry["max_risk_score"],
                "risk_score_sum": entry["risk_score_sum"],
                "categories": entry["categories"],
            }
            for entry in selected_files
            if self._safe_int(entry.get("max_risk_score") or 0) >= 2
        ]

        payload = {
            "summary": {
                "max_files": max_files,
                "total_dead_symbols": (
                    raw_total_dead_symbols
                    if raw_total_dead_symbols is not None
                    else len(dead_functions)
                ),
                "filtered_dead_symbols": len(filtered_items),
                "candidate_files": len(file_map),
                "selected_files": len(selected_files),
                "category_totals": category_totals,
                "high_risk_files": len(high_risk_files),
                "suppressed_dead_symbols": int(suppressed_dead_symbols or 0),
                "suppression_reasons": suppression_reason_counts or {},
                "confidence": "medium",
                "do_not_delete_blindly": True,
                "review_policy": "manual_review_required",
                "warning": (
                    "Heuristic dead-code candidates only. Dynamic registrations, "
                    "framework wiring, router assembly, generated clients, SQL runtime hooks, "
                    "and local symbol references can produce false positives."
                ),
            },
            "high_risk_files": high_risk_files,
            "files": selected_files,
        }
        report_path = self._write_json_report("dead-code-except-test.json", payload)
        return {
            "report_path": str(report_path),
            "selected_files": len(selected_files),
            "filtered_dead_symbols": len(filtered_items),
        }

    def _filter_dead_code_candidates(
        self: AnalysisRunnerProtocol,
        dead_functions: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        filtered: list[dict[str, Any]] = []
        suppression_reason_counts: dict[str, int] = {}

        for item in dead_functions:
            reasons = self._dead_code_suppression_reasons(item)
            if reasons:
                for reason in reasons:
                    suppression_reason_counts[reason] = (
                        suppression_reason_counts.get(reason, 0) + 1
                    )
                continue
            filtered.append(item)

        return filtered, suppression_reason_counts

    def _build_dead_code_report_payload(
        self: AnalysisRunnerProtocol,
        *,
        total_functions: int,
        dead_functions: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, int]]:
        filtered_dead_functions, suppression_reason_counts = (
            self._filter_dead_code_candidates(dead_functions)
        )
        payload = {
            "summary": {
                "total_functions": total_functions,
                "raw_dead_functions": len(dead_functions),
                "reported_dead_functions": len(filtered_dead_functions),
                "suppressed_dead_functions": len(dead_functions)
                - len(filtered_dead_functions),
                "suppression_reasons": suppression_reason_counts,
                "confidence": "medium",
                "do_not_delete_blindly": True,
                "review_policy": "manual_review_required",
                "warning": (
                    "Heuristic dead-code candidates only. Framework/runtime registrations, "
                    "frontend route assembly, generated clients, local JS/TS symbol references, "
                    "and SQL trigger wiring can hide real usage."
                ),
            },
            "total_functions": total_functions,
            "dead_functions": filtered_dead_functions,
        }
        return payload, filtered_dead_functions, suppression_reason_counts

    def _dead_code_suppression_reasons(
        self: AnalysisRunnerProtocol,
        item: dict[str, Any],
    ) -> list[str]:
        path = self._normalize_dead_code_path(str(item.get("path") or ""))
        name = str(item.get("name") or "").strip()
        if not path or not name:
            return ["missing_symbol_identity"]

        reasons: list[str] = []
        if self._is_test_dead_code_item(path, name):
            reasons.append("test_path")
        if self._is_generated_or_noise_path(path):
            reasons.append("generated_or_noise_path")
        if not self._is_runtime_source_path(path):
            reasons.append("non_runtime_source")
        if DeadCodeExportsMixin._is_anonymous_callback_symbol(name):
            reasons.append("anonymous_callback")
        if DeadCodeExportsMixin._is_python_package_reexport(self, path, name):
            reasons.append("python_package_reexport")

        source_text = self._read_dead_code_source_text(path)
        if source_text:
            if self._has_sql_runtime_registration(path, name, source_text):
                reasons.append("sql_runtime_registration")
            if self._is_frontend_route_registration(path, name, source_text):
                reasons.append("frontend_route_registration")
            if self._is_python_delegating_wrapper(path, name, source_text):
                reasons.append("python_delegating_wrapper")
            if self._is_source_exported_symbol(path, name, source_text):
                reasons.append("source_exported_symbol")
            if self._has_local_symbol_references(path, name, source_text):
                reasons.append("local_symbol_reference")

        return list(dict.fromkeys(reasons))

    def _read_dead_code_source_text(
        self: AnalysisRunnerProtocol,
        path: str,
    ) -> str:
        cache = getattr(self, "_dead_code_source_text_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, "_dead_code_source_text_cache", cache)
        if path in cache:
            return cast(str, cache[path])

        file_path = self.repo_path / path
        if not file_path.exists() or not file_path.is_file():
            cache[path] = ""
            return ""
        try:
            source_text = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            source_text = ""
        cache[path] = source_text
        return source_text

    @staticmethod
    def _count_symbol_occurrences(source_text: str, name: str) -> int:
        if not source_text or not name:
            return 0
        pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])")
        return len(pattern.findall(source_text))

    def _has_local_symbol_references(
        self,
        path: str,
        name: str,
        source_text: str,
    ) -> bool:
        suffix = Path(path).suffix.lower()
        if suffix not in {".js", ".jsx", ".ts", ".tsx"}:
            return False
        return self._count_symbol_occurrences(source_text, name) > 1

    def _is_source_exported_symbol(
        self,
        path: str,
        name: str,
        source_text: str,
    ) -> bool:
        suffix = Path(path).suffix.lower()
        if suffix not in {".js", ".jsx", ".ts", ".tsx"}:
            return False
        patterns = (
            rf"^\s*export\s+(?:const|let|var|class|function|async\s+function)\s+{re.escape(name)}\b",
            rf"^\s*export\s+default\s+(?:function|class)\s+{re.escape(name)}\b",
            rf"^\s*export\s*\{{[^}}]*\b{re.escape(name)}\b[^}}]*\}}",
        )
        return any(
            re.search(pattern, source_text, re.IGNORECASE | re.MULTILINE)
            for pattern in patterns
        )

    def _is_frontend_route_registration(
        self,
        path: str,
        name: str,
        source_text: str,
    ) -> bool:
        suffix = Path(path).suffix.lower()
        if suffix not in {".js", ".jsx", ".ts", ".tsx"}:
            return False
        if not path.startswith("frontend/"):
            return False
        if name == "routeModuleLoaders":
            return "lazy(" in source_text or "preloadRouteModule" in source_text
        if not name.endswith("RouteScreen"):
            return False
        return "lazy(" in source_text or "withLazyRoute(" in source_text

    def _has_sql_runtime_registration(
        self,
        path: str,
        name: str,
        source_text: str,
    ) -> bool:
        if Path(path).suffix.lower() != ".sql":
            return False
        pattern = re.compile(
            rf"EXECUTE\s+FUNCTION\s+{re.escape(name)}\s*\(",
            re.IGNORECASE,
        )
        return bool(pattern.search(source_text))

    def _is_python_delegating_wrapper(
        self,
        path: str,
        name: str,
        source_text: str,
    ) -> bool:
        if Path(path).suffix.lower() != ".py":
            return False
        try:
            module = ast.parse(source_text)
        except SyntaxError:
            return False

        imported_aliases: dict[str, str] = {}
        for node in module.body:
            if not isinstance(node, ast.ImportFrom):
                continue
            for alias in node.names:
                bound_name = alias.asname or alias.name
                imported_aliases[bound_name] = alias.name

        if not imported_aliases:
            return False

        for node in module.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if node.name != name:
                continue
            body = list(node.body)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]
            if len(body) != 1:
                return False
            delegated_call = self._extract_python_delegated_call_name(body[0])
            if delegated_call is None:
                return False
            imported_original_name = imported_aliases.get(delegated_call)
            if imported_original_name is None:
                return False
            if node.name.lstrip("_") != imported_original_name.lstrip("_"):
                return False
            return True
        return False

    @staticmethod
    def _extract_python_delegated_call_name(statement: ast.stmt) -> str | None:
        expression: ast.expr | None = None
        if isinstance(statement, ast.Return):
            expression = statement.value
        elif isinstance(statement, ast.Expr):
            expression = statement.value

        if expression is None:
            return None
        if isinstance(expression, ast.Await):
            expression = expression.value
        if not isinstance(expression, ast.Call):
            return None
        if isinstance(expression.func, ast.Name):
            return expression.func.id
        return None
