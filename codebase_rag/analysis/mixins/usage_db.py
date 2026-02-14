import json
from datetime import UTC, datetime
from typing import Any, cast

from loguru import logger

from codebase_rag.core import constants as cs
from codebase_rag.graph_db.cypher_queries import (
    CYPHER_ANALYSIS_DEAD_CODE,
    CYPHER_ANALYSIS_DEAD_CODE_FILTERED,
    CYPHER_ANALYSIS_TOTAL_FUNCTIONS,
    CYPHER_ANALYSIS_TOTAL_FUNCTIONS_FILTERED,
    CYPHER_ANALYSIS_UNUSED_IMPORTS,
    CYPHER_ANALYSIS_UNUSED_IMPORTS_FILTERED,
    CYPHER_ANALYSIS_USAGE,
    CYPHER_ANALYSIS_USAGE_FILTERED,
)

from ...services.protocols import QueryProtocol
from ..protocols import AnalysisRunnerProtocol


class UsageDbMixin:
    def _symbol_usage_db(
        self: AnalysisRunnerProtocol, module_paths: list[str] | None
    ) -> dict[str, Any]:
        query = (
            CYPHER_ANALYSIS_USAGE_FILTERED if module_paths else CYPHER_ANALYSIS_USAGE
        )
        params: dict[str, object] = {cs.KEY_PROJECT_NAME: self.project_name}
        if module_paths:
            params["module_paths"] = module_paths

        ingestor = cast(QueryProtocol, self.ingestor)
        rows = ingestor.fetch_all(query, cast(Any, params))
        for row in rows:
            qn = str(cast(Any, row.get(cs.KEY_QUALIFIED_NAME)) or "")
            label = str(cast(Any, row.get("label")) or "")
            count = int(cast(Any, row.get("usage_count")) or 0)
            if not qn or not label:
                continue
            self.ingestor.ensure_node_batch(
                label,
                {
                    cs.KEY_QUALIFIED_NAME: qn,
                    "usage_count": count,
                },
            )
        return {
            "symbols_with_usage": len(rows),
            "total_usage_edges": sum(
                int(cast(Any, r.get("usage_count")) or 0) for r in rows
            ),
        }

    def _dead_code_report_db(
        self: AnalysisRunnerProtocol, module_paths: list[str] | None
    ) -> dict[str, Any]:
        entry_points = [
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
        ]
        decorators = [
            "@route",
            "@controller",
            "@component",
            "@injectable",
            "@public",
        ]
        query = (
            CYPHER_ANALYSIS_DEAD_CODE_FILTERED
            if module_paths
            else CYPHER_ANALYSIS_DEAD_CODE
        )
        total_query = (
            CYPHER_ANALYSIS_TOTAL_FUNCTIONS_FILTERED
            if module_paths
            else CYPHER_ANALYSIS_TOTAL_FUNCTIONS
        )
        params: dict[str, object] = {
            cs.KEY_PROJECT_NAME: self.project_name,
            "entry_names": entry_points,
            "decorators": decorators,
        }
        if module_paths:
            params["module_paths"] = module_paths

        ingestor = cast(QueryProtocol, self.ingestor)
        rows = ingestor.fetch_all(query, cast(Any, params))
        total_rows = ingestor.fetch_all(total_query, cast(Any, params))
        total_functions = 0
        if total_rows:
            total_functions = int(cast(Any, total_rows[0].get("total_functions")) or 0)

        report = {
            "total_functions": total_functions,
            "dead_functions": [
                {
                    "qualified_name": row.get(cs.KEY_QUALIFIED_NAME),
                    "name": row.get(cs.KEY_NAME),
                    "path": row.get(cs.KEY_PATH),
                    "start_line": row.get(cs.KEY_START_LINE),
                    "label": row.get("label") or cs.NodeLabel.FUNCTION,
                    "call_in_degree": int(cast(Any, row.get("call_in_degree")) or 0),
                    "out_call_count": int(cast(Any, row.get("out_call_count")) or 0),
                    "is_entrypoint_name": bool(
                        cast(Any, row.get("is_entrypoint_name"))
                    ),
                    "has_entry_decorator": bool(
                        cast(Any, row.get("has_entry_decorator"))
                    ),
                    "decorator_links": int(cast(Any, row.get("decorator_links")) or 0),
                    "registration_links": int(
                        cast(Any, row.get("registration_links")) or 0
                    ),
                    "imported_by_cli_links": int(
                        cast(Any, row.get("imported_by_cli_links")) or 0
                    ),
                    "config_reference_links": int(
                        cast(Any, row.get("config_reference_links")) or 0
                    ),
                    "decorators": row.get(cs.KEY_DECORATORS)
                    or row.get("decorators")
                    or [],
                    "is_exported": bool(
                        cast(Any, row.get(cs.KEY_IS_EXPORTED)) or row.get("is_exported")
                    ),
                }
                for row in rows
            ],
        }

        dead_functions = cast(list[dict[str, Any]], report["dead_functions"])

        analysis_run_id = datetime.now(UTC).replace(microsecond=0).isoformat()
        self._apply_dead_code_node_cache(
            dead_functions,
            analysis_run_id=analysis_run_id,
        )

        except_test_summary = self._write_dead_code_except_test_report(
            dead_functions,
            max_files=200,
        )

        output_dir = self.repo_path / "output" / "analysis"
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "dead_code_report.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        logger.info("Dead code report saved: {}", report_path)

        return {
            "total_functions": report["total_functions"],
            "dead_functions": report["dead_functions"],
            "dead_code_except_test": except_test_summary,
        }

    def _unused_imports_db(
        self: AnalysisRunnerProtocol, module_paths: list[str] | None
    ) -> dict[str, Any]:
        query = (
            CYPHER_ANALYSIS_UNUSED_IMPORTS_FILTERED
            if module_paths
            else CYPHER_ANALYSIS_UNUSED_IMPORTS
        )
        params: dict[str, object] = {cs.KEY_PROJECT_NAME: self.project_name}
        if module_paths:
            params["module_paths"] = module_paths

        ingestor = cast(QueryProtocol, self.ingestor)
        rows = ingestor.fetch_all(query, cast(Any, params))
        report_payload = {
            "summary": {
                "unused_imports": len(rows),
                "files_with_unused": len(
                    {
                        str(row.get(cs.KEY_PATH) or "")
                        for row in rows
                        if row.get(cs.KEY_PATH)
                    }
                ),
                "source": "db",
            },
            "reason": (
                "No unused import candidate found in graph" if not rows else None
            ),
            "findings": rows,
        }
        output_dir = self.repo_path / "output" / "analysis"
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "unused_imports_report.json"
        report_path.write_text(json.dumps(report_payload, indent=2), encoding="utf-8")
        return {
            "unused_imports": len(rows),
            "files_with_unused": len({row.get(cs.KEY_PATH) for row in rows}),
        }
