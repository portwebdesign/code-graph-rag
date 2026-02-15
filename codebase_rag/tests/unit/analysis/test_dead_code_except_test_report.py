from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from codebase_rag.analysis.analysis_runner import AnalysisRunner
from codebase_rag.services import IngestorProtocol


class DummyIngestor:
    def fetch_all(self, query: str, params: dict[str, Any] | None = None):
        if "total_functions" in query.lower():
            return [{"total_functions": 8}]
        return [
            {
                "qualified_name": "proj.codebase_rag.core.cli.export",
                "name": "export",
                "path": "codebase_rag/core/cli.py",
                "start_line": 320,
            },
            {
                "qualified_name": "proj.codebase_rag.logs.__getattr__",
                "name": "__getattr__",
                "path": "codebase_rag/logs.py",
                "start_line": 11,
            },
            {
                "qualified_name": "proj.codebase_rag.tests.test_a.test_x",
                "name": "test_x",
                "path": "codebase_rag/tests/test_a.py",
                "start_line": 10,
            },
            {
                "qualified_name": "proj.output.analysis.anon",
                "name": "anonymous",
                "path": "output/analysis/a.json",
                "start_line": 1,
            },
        ]

    def ensure_node_batch(self, label: str, props: dict[str, Any]) -> None:
        return None

    def ensure_relationship_batch(self, *args: Any, **kwargs: Any) -> None:
        return None

    def flush_all(self) -> None:
        return None


def test_dead_code_except_test_report_created(tmp_path: Path) -> None:
    runner = AnalysisRunner(cast(IngestorProtocol, DummyIngestor()), tmp_path)
    result = runner._dead_code_report_db(module_paths=None)

    assert "dead_code_except_test" in result
    report_path = tmp_path / "output" / "analysis" / "dead-code-except-test.json"
    assert report_path.exists()

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["summary"]["selected_files"] >= 1
    assert all(
        "tests" not in str(file_entry["path"]).lower()
        for file_entry in payload["files"]
    )
    assert all(
        not str(file_entry["path"]).lower().startswith("output/")
        for file_entry in payload["files"]
    )


def test_dead_code_except_test_report_has_categories(tmp_path: Path) -> None:
    runner = AnalysisRunner(cast(IngestorProtocol, DummyIngestor()), tmp_path)
    payload = runner._write_dead_code_except_test_report(
        [
            {
                "qualified_name": "proj.codebase_rag.core.cli.graph_loader_command",
                "name": "graph_loader_command",
                "path": "codebase_rag/core/cli.py",
                "start_line": 462,
            },
            {
                "qualified_name": "proj.codebase_rag.logs.__dir__",
                "name": "__dir__",
                "path": "codebase_rag/logs.py",
                "start_line": 15,
            },
            {
                "qualified_name": "proj.codebase_rag.mcp.tools.my_tool",
                "name": "my_tool",
                "path": "codebase_rag/mcp/tools.py",
                "start_line": 40,
                "registration_links": 1,
                "decorator_links": 0,
                "imported_by_cli_links": 0,
                "config_reference_links": 0,
            },
            {
                "qualified_name": "proj.codebase_rag.domain.payment.reconcile",
                "name": "reconcile",
                "path": "codebase_rag/domain/payment.py",
                "start_line": 77,
            },
        ]
    )

    assert payload["selected_files"] == 4
    report_path = tmp_path / "output" / "analysis" / "dead-code-except-test.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    categories = report["summary"]["category_totals"]
    assert "cli_or_entrypoint" in categories
    assert "dynamic_or_magic" in categories
    assert "framework_registered" in categories
    assert report["summary"]["high_risk_files"] >= 1


def test_dead_code_except_test_report_contains_graph_confidence_and_risk(
    tmp_path: Path,
) -> None:
    runner = AnalysisRunner(cast(IngestorProtocol, DummyIngestor()), tmp_path)
    runner._write_dead_code_except_test_report(
        [
            {
                "qualified_name": "proj.codebase_rag.core.service.fn",
                "name": "fn",
                "path": "codebase_rag/core/service.py",
                "start_line": 10,
                "call_in_degree": 0,
                "decorator_links": 0,
                "registration_links": 0,
                "imported_by_cli_links": 0,
                "config_reference_links": 0,
            }
        ]
    )

    report_path = tmp_path / "output" / "analysis" / "dead-code-except-test.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    symbol = report["files"][0]["dead_symbols"][0]
    assert "risk_score" in symbol
    assert "graph_confidence" in symbol
    assert symbol["graph_confidence"]["call_in_degree"] == 0
