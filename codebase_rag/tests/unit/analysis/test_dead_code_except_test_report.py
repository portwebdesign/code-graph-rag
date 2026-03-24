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


class DecoratedEntryPointIngestor:
    def __init__(self) -> None:
        self.captured_query = ""
        self.captured_params: dict[str, Any] | None = None

    def fetch_all(self, query: str, params: dict[str, Any] | None = None):
        self.captured_query = query
        self.captured_params = params
        if "total_functions" in query.lower():
            return [{"total_functions": 1}]
        return []

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
                "dispatch_in_degree": 1,
                "combined_in_degree": 1,
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
    assert symbol["graph_confidence"]["dispatch_in_degree"] == 1
    assert symbol["graph_confidence"]["combined_in_degree"] == 1
    assert symbol["reachability_source"] == "dispatch_reference"


def test_dead_code_report_payload_retains_combined_liveness_metadata(
    tmp_path: Path,
) -> None:
    runner = AnalysisRunner(cast(IngestorProtocol, DummyIngestor()), tmp_path)

    report, filtered_dead_functions, _ = runner._build_dead_code_report_payload(
        total_functions=2,
        dead_functions=[
            {
                "qualified_name": "proj.dispatch.handlers._handle_status",
                "name": "_handle_status",
                "path": "src/handlers.py",
                "start_line": 10,
                "call_in_degree": 0,
                "dispatch_in_degree": 1,
                "combined_in_degree": 1,
                "decorator_links": 0,
                "registration_links": 0,
                "imported_by_cli_links": 0,
                "config_reference_links": 0,
            }
        ],
    )

    assert report["summary"]["reported_dead_functions"] == 1
    assert filtered_dead_functions[0]["dispatch_in_degree"] == 1
    assert filtered_dead_functions[0]["combined_in_degree"] == 1


def test_dead_code_report_payload_suppresses_noise_and_adds_guidance(
    tmp_path: Path,
) -> None:
    runner = AnalysisRunner(cast(IngestorProtocol, DummyIngestor()), tmp_path)

    route_modules = tmp_path / "frontend" / "src" / "app" / "routeModules.tsx"
    route_modules.parent.mkdir(parents=True, exist_ok=True)
    route_modules.write_text(
        """import { lazy } from \"react\";

const routeModuleLoaders = {
  \"/dashboard\": () => import(\"@/features/screens/DashboardScreen\"),
};

export const DashboardRouteScreen = lazy(() =>
  import(\"@/features/screens/DashboardScreen\").then((module) => ({ default: module.DashboardScreen })),
);

export async function preloadRouteModule() {
  await routeModuleLoaders[\"/dashboard\"]();
}
""",
        encoding="utf-8",
    )

    smoke_spec = tmp_path / "frontend" / "e2e" / "smoke.spec.ts"
    smoke_spec.parent.mkdir(parents=True, exist_ok=True)
    smoke_spec.write_text(
        """function installApiMocks() {
  const existingSession = { id: 1 };
  return existingSession;
}
""",
        encoding="utf-8",
    )

    sql_file = tmp_path / "docker" / "postgres" / "init" / "02-schema.sql"
    sql_file.parent.mkdir(parents=True, exist_ok=True)
    sql_file.write_text(
        """CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_updated_at
BEFORE UPDATE ON tenants
FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();
""",
        encoding="utf-8",
    )

    candidate_file = tmp_path / "src" / "domain" / "payment.py"
    candidate_file.parent.mkdir(parents=True, exist_ok=True)
    candidate_file.write_text(
        """def reconcile():
    return 42
""",
        encoding="utf-8",
    )

    report, filtered_dead_functions, suppression_reason_counts = (
        runner._build_dead_code_report_payload(
            total_functions=4,
            dead_functions=[
                {
                    "qualified_name": "abey.frontend.src.app.routeModules.routeModuleLoaders",
                    "name": "routeModuleLoaders",
                    "path": "frontend/src/app/routeModules.tsx",
                    "start_line": 3,
                },
                {
                    "qualified_name": "abey.frontend.src.app.routeModules.DashboardRouteScreen",
                    "name": "DashboardRouteScreen",
                    "path": "frontend/src/app/routeModules.tsx",
                    "start_line": 7,
                },
                {
                    "qualified_name": "abey.frontend.e2e.smoke.spec.installApiMocks.existingSession",
                    "name": "existingSession",
                    "path": "frontend/e2e/smoke.spec.ts",
                    "start_line": 2,
                },
                {
                    "qualified_name": "abey.docker.postgres.init.02-schema.trigger_set_updated_at",
                    "name": "trigger_set_updated_at",
                    "path": "docker/postgres/init/02-schema.sql",
                    "start_line": 1,
                },
                {
                    "qualified_name": "abey.src.domain.payment.reconcile",
                    "name": "reconcile",
                    "path": "src/domain/payment.py",
                    "start_line": 1,
                },
            ],
        )
    )

    assert report["summary"]["do_not_delete_blindly"] is True
    assert report["summary"]["confidence"] == "medium"
    assert report["summary"]["reported_dead_functions"] == 1
    assert report["summary"]["suppressed_dead_functions"] == 4
    assert suppression_reason_counts["test_path"] >= 1
    assert suppression_reason_counts["non_runtime_source"] >= 1
    assert suppression_reason_counts["frontend_route_registration"] >= 1
    assert suppression_reason_counts["source_exported_symbol"] >= 1
    assert suppression_reason_counts["local_symbol_reference"] >= 1
    assert [item["qualified_name"] for item in filtered_dead_functions] == [
        "abey.src.domain.payment.reconcile"
    ]


def test_dead_code_report_db_query_excludes_decorated_entry_points(
    tmp_path: Path,
) -> None:
    ingestor = DecoratedEntryPointIngestor()
    runner = AnalysisRunner(cast(IngestorProtocol, ingestor), tmp_path)

    runner._dead_code_report_db(module_paths=None)

    assert "coalesce(f.is_entry_point, false) = false" in ingestor.captured_query
    assert "[:DECORATES|ANNOTATES]" in ingestor.captured_query
    assert "[:DISPATCHES_TO]" in ingestor.captured_query
    assert "[:USES_DEPENDENCY|SECURED_BY]" in ingestor.captured_query
    assert "[:RESOLVES_TO]->(f)" in ingestor.captured_query
    assert "semantic_registration_links" in ingestor.captured_query
    assert "combined_in_degree" in ingestor.captured_query
    assert (
        "HAS_ENDPOINT|ROUTES_TO_CONTROLLER|ROUTES_TO_ACTION|REQUESTS_ENDPOINT|REGISTERS_SERVICE|REGISTERS_CALLBACK|HOOKS|REGISTERS_BLOCK|USES_HANDLER|USES_SERVICE|PROVIDES_SERVICE"
        in ingestor.captured_query
    )


def test_dead_code_report_payload_suppresses_iife_build_config_and_python_reexports(
    tmp_path: Path,
) -> None:
    runner = AnalysisRunner(cast(IngestorProtocol, DummyIngestor()), tmp_path)

    shell_frame = (
        tmp_path / "frontend" / "src" / "features" / "shell" / "ShellLayoutFrame.tsx"
    )
    shell_frame.parent.mkdir(parents=True, exist_ok=True)
    shell_frame.write_text(
        """export function ShellLayoutFrame() {
  void (async () => {
    await Promise.resolve();
  })();
  return null;
}
""",
        encoding="utf-8",
    )

    vite_config = tmp_path / "frontend" / "vite.config.ts"
    vite_config.parent.mkdir(parents=True, exist_ok=True)
    vite_config.write_text(
        """export default {
  build: {
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          return id.includes("node_modules") ? "vendor" : undefined;
        },
      },
    },
  },
};
""",
        encoding="utf-8",
    )

    cli_handlers = (
        tmp_path / "src" / "workers" / "schema_sync" / "governance" / "cli_handlers.py"
    )
    cli_handlers.parent.mkdir(parents=True, exist_ok=True)
    cli_handlers.write_text(
        """def build_parser():
    return object()


async def run_cli(args, *, services):
    return 0
""",
        encoding="utf-8",
    )

    package_init = cli_handlers.with_name("__init__.py")
    package_init.write_text(
        """from .cli_handlers import build_parser as build_governance_parser, run_cli as run_governance_cli

__all__ = ["build_parser", "run_cli"]


def build_parser():
    return build_governance_parser()


async def run_cli(args):
    return await run_governance_cli(args, services=None)
""",
        encoding="utf-8",
    )

    candidate_file = tmp_path / "src" / "domain" / "payment.py"
    candidate_file.parent.mkdir(parents=True, exist_ok=True)
    candidate_file.write_text(
        """def reconcile():
    return 42
""",
        encoding="utf-8",
    )

    report, filtered_dead_functions, suppression_reason_counts = (
        runner._build_dead_code_report_payload(
            total_functions=6,
            dead_functions=[
                {
                    "qualified_name": "abey.frontend.src.features.shell.ShellLayoutFrame.ShellLayoutFrame.iife_arrow_1_7",
                    "name": "iife_arrow_1_7",
                    "path": "frontend/src/features/shell/ShellLayoutFrame.tsx",
                    "start_line": 2,
                },
                {
                    "qualified_name": "abey.frontend.src.features.shell.ShellLayoutFrame.ShellLayoutFrame.iife_func_1_7",
                    "name": "iife_func_1_7",
                    "path": "frontend/src/features/shell/ShellLayoutFrame.tsx",
                    "start_line": 2,
                },
                {
                    "qualified_name": "abey.frontend.vite.config.manualChunks",
                    "name": "manualChunks",
                    "path": "frontend/vite.config.ts",
                    "start_line": 5,
                },
                {
                    "qualified_name": "abey.src.workers.schema_sync.governance.cli_handlers.build_parser",
                    "name": "build_parser",
                    "path": "src/workers/schema_sync/governance/cli_handlers.py",
                    "start_line": 1,
                },
                {
                    "qualified_name": "abey.src.workers.schema_sync.governance.cli_handlers.run_cli",
                    "name": "run_cli",
                    "path": "src/workers/schema_sync/governance/cli_handlers.py",
                    "start_line": 5,
                },
                {
                    "qualified_name": "abey.src.domain.payment.reconcile",
                    "name": "reconcile",
                    "path": "src/domain/payment.py",
                    "start_line": 1,
                },
            ],
        )
    )

    assert [item["qualified_name"] for item in filtered_dead_functions] == [
        "abey.src.domain.payment.reconcile"
    ]
    assert suppression_reason_counts["anonymous_callback"] >= 2
    assert suppression_reason_counts["non_runtime_source"] >= 1
    assert suppression_reason_counts["python_package_reexport"] >= 2


def test_dead_code_report_payload_suppresses_python_delegating_wrappers(
    tmp_path: Path,
) -> None:
    runner = AnalysisRunner(cast(IngestorProtocol, DummyIngestor()), tmp_path)

    lifecycle_file = tmp_path / "src" / "api" / "lifecycle.py"
    lifecycle_file.parent.mkdir(parents=True, exist_ok=True)
    lifecycle_file.write_text(
        """from src.api.lifecycle_schema_stream import (\n    schema_update_listener_task as run_schema_update_listener,\n)\n\n\nasync def schema_update_listener_task(app):\n    await run_schema_update_listener(app)\n""",
        encoding="utf-8",
    )

    candidate_file = tmp_path / "src" / "domain" / "payment.py"
    candidate_file.parent.mkdir(parents=True, exist_ok=True)
    candidate_file.write_text(
        """def reconcile():\n    return 42\n""",
        encoding="utf-8",
    )

    report, filtered_dead_functions, suppression_reason_counts = (
        runner._build_dead_code_report_payload(
            total_functions=2,
            dead_functions=[
                {
                    "qualified_name": "proj.src.api.lifecycle.schema_update_listener_task",
                    "name": "schema_update_listener_task",
                    "path": "src/api/lifecycle.py",
                    "start_line": 5,
                },
                {
                    "qualified_name": "proj.src.domain.payment.reconcile",
                    "name": "reconcile",
                    "path": "src/domain/payment.py",
                    "start_line": 1,
                },
            ],
        )
    )

    assert report["summary"]["reported_dead_functions"] == 1
    assert [item["qualified_name"] for item in filtered_dead_functions] == [
        "proj.src.domain.payment.reconcile"
    ]
    assert suppression_reason_counts["python_delegating_wrapper"] >= 1


def test_dead_code_except_test_report_includes_guidance_summary(tmp_path: Path) -> None:
    runner = AnalysisRunner(cast(IngestorProtocol, DummyIngestor()), tmp_path)
    runner._write_dead_code_except_test_report(
        [
            {
                "qualified_name": "proj.codebase_rag.domain.payment.reconcile",
                "name": "reconcile",
                "path": "codebase_rag/domain/payment.py",
                "start_line": 77,
            }
        ],
        raw_total_dead_symbols=5,
        suppression_reason_counts={"test_path": 2, "non_runtime_source": 2},
        suppressed_dead_symbols=4,
    )

    report_path = tmp_path / "output" / "analysis" / "dead-code-except-test.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    summary = report["summary"]
    assert summary["do_not_delete_blindly"] is True
    assert summary["confidence"] == "medium"
    assert summary["suppressed_dead_symbols"] == 4
    assert summary["suppression_reasons"]["test_path"] == 2
