from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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

        return True

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
        return {
            "call_in_degree": self._safe_int(item.get("call_in_degree") or 0),
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
        if self._truthy(item.get("is_entrypoint_name")):
            return True, "entrypoint"
        if self._truthy(item.get("has_entry_decorator")):
            return True, "decorator"
        if graph_confidence["decorator_links"] > 0:
            return True, "decorator"
        if graph_confidence["registration_links"] > 0:
            return True, "framework_registration"
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
        if graph_confidence["call_in_degree"] == 0:
            score += 50
        if not self._truthy(item.get("is_exported")):
            score += 20
        if not self._truthy(item.get("is_entrypoint_name")):
            score += 20
        if graph_confidence["decorator_links"] == 0 and not self._truthy(
            item.get("has_entry_decorator")
        ):
            score += 10
        score -= min(60, graph_confidence["registration_links"] * 20)
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
                "total_dead_symbols": len(dead_functions),
                "filtered_dead_symbols": len(filtered_items),
                "candidate_files": len(file_map),
                "selected_files": len(selected_files),
                "category_totals": category_totals,
                "high_risk_files": len(high_risk_files),
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
