from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from codebase_rag.core import constants as cs

from ..services.protocols import IngestorProtocol, QueryProtocol
from ..utils.git_delta import get_git_head
from .dead_code_verifier import verify_dead_code
from .mixins import (
    AnalysisConfigMixin,
    AnalysisGraphAccessMixin,
    ComplexityMixin,
    DeadCodeExportsMixin,
    DependenciesMixin,
    HotspotsMixin,
    MigrationPlanMixin,
    OutputUtilsMixin,
    QualityMixin,
    SecurityAuditMixin,
    SecurityMixin,
    StaticChecksMixin,
    StructureMixin,
    SupplementalAnalysisMixin,
    TopologyMixin,
    TrendsMixin,
    UsageDbMixin,
    UsageInMemoryMixin,
)
from .modules import (
    AnalysisContext,
    ApiCallChainModule,
    ApiComplianceModule,
    ComplexityModule,
    DeadCodeAIModule,
    DeadCodeModule,
    DependenciesModule,
    DependencyHealthModule,
    DocumentationQualityModule,
    FrameworkMatcherModule,
    HotspotsModule,
    MigrationModule,
    MLInsightsModule,
    PerformanceAnalysisModule,
    SchemaValidatorModule,
    SecurityModule,
)
from .protocols import AnalysisRunnerProtocol
from .types import NodeRecord, RelationshipRecord


class AnalysisRunner(
    AnalysisConfigMixin,
    DeadCodeExportsMixin,
    AnalysisGraphAccessMixin,
    ComplexityMixin,
    DependenciesMixin,
    HotspotsMixin,
    MigrationPlanMixin,
    OutputUtilsMixin,
    QualityMixin,
    SecurityMixin,
    SecurityAuditMixin,
    StaticChecksMixin,
    StructureMixin,
    SupplementalAnalysisMixin,
    TopologyMixin,
    TrendsMixin,
    UsageDbMixin,
    UsageInMemoryMixin,
    AnalysisRunnerProtocol,
):
    def __init__(self, ingestor: IngestorProtocol, repo_path: Path) -> None:
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.project_name = repo_path.resolve().name

    def run_all(self) -> None:
        if not isinstance(self.ingestor, QueryProtocol):
            logger.info("Analysis skipped: ingestor does not support queries")
            return

        modules = self._resolve_modules()
        self.run_modules(modules)

    def run_modules(
        self,
        modules: set[str] | None = None,
        incremental_paths: list[str] | None = None,
    ) -> dict[str, object]:
        if not isinstance(self.ingestor, QueryProtocol):
            logger.info("Analysis skipped: ingestor does not support queries")
            return {}

        incremental_paths = (
            incremental_paths
            if incremental_paths is not None
            else self._get_incremental_paths()
        )
        fast_incremental = str(
            os.getenv("CODEGRAPH_ANALYSIS_INCREMENTAL_FAST", "")
        ).lower() in {"1", "true", "yes"}
        if incremental_paths is not None and fast_incremental and modules is None:
            modules = {"dead_code", "unused_imports"}
        module_paths = self._resolve_module_paths(incremental_paths)
        use_db = str(os.getenv("CODEGRAPH_ANALYSIS_DB", "1")).lower() not in {
            "0",
            "false",
            "no",
        }
        if incremental_paths is not None and fast_incremental:
            use_db = True

        needs_graph = self._needs_graph_data(modules, use_db)
        nodes: list[NodeRecord] = []
        relationships: list[RelationshipRecord] = []
        if needs_graph:
            nodes, relationships = self._load_graph_data(self.ingestor)

        if nodes:
            module_path_map = self._build_module_path_map(nodes)
            node_by_id = {node.node_id: node for node in nodes}
        else:
            module_path_map = {}
            node_by_id = {}

        summary: dict[str, object] = {}

        context = AnalysisContext(
            runner=self,
            nodes=nodes,
            relationships=relationships,
            module_path_map=module_path_map,
            node_by_id=node_by_id,
            module_paths=module_paths,
            incremental_paths=incremental_paths,
            use_db=use_db,
            summary=summary,
            dead_code_verifier=self._get_dead_code_verifier(),
        )

        module_registry = self._build_default_modules()
        module_names = {module.get_name() for module in module_registry}
        for module in module_registry:
            name = module.get_name()
            if not self._should_run(name, modules):
                continue
            result = module.run(context)
            if result:
                summary[name] = result

        self._run_supplemental_analyses(
            summary,
            modules,
            module_names,
            nodes,
            relationships,
            node_by_id,
            module_path_map,
            module_paths,
            incremental_paths,
            use_db,
        )

        if summary:
            self._write_analysis_report(summary)

        self.ingestor.flush_all()

        if incremental_paths is not None and fast_incremental:
            return summary

        nodes, relationships = self._load_graph_data(self.ingestor)
        if not nodes:
            logger.info("Analysis skipped: no nodes found")
            return {}

        return summary

    @staticmethod
    def _build_default_modules() -> list:
        return [
            ComplexityModule(),
            DeadCodeModule(),
            DeadCodeAIModule(),
            SecurityModule(),
            SchemaValidatorModule(),
            DependenciesModule(),
            HotspotsModule(),
            MigrationModule(),
            MLInsightsModule(),
            FrameworkMatcherModule(),
            PerformanceAnalysisModule(),
            DependencyHealthModule(),
            ApiComplianceModule(),
            DocumentationQualityModule(),
            ApiCallChainModule(),
        ]

    @staticmethod
    def _get_dead_code_verifier():
        enabled = str(os.getenv("CODEGRAPH_DEAD_CODE_VERIFY", "")).lower() in {
            "1",
            "true",
            "yes",
        }
        if not enabled:
            return None
        return verify_dead_code

    def _write_analysis_report(self, summary: dict[str, object]) -> None:
        if not isinstance(self.ingestor, QueryProtocol):
            return

        timestamp = datetime.now(UTC).replace(microsecond=0).isoformat()
        run_id = f"run-{int(time.time() * 1000)}"
        run_qn = f"{self.project_name}{cs.SEPARATOR_DOT}analysis_run.{run_id}"
        report_qn = f"{self.project_name}{cs.SEPARATOR_DOT}analysis_report.{run_id}"
        git_head = get_git_head(self.repo_path)

        self.ingestor.ensure_node_batch(
            cs.NodeLabel.ANALYSIS_RUN,
            {
                cs.KEY_QUALIFIED_NAME: run_qn,
                cs.KEY_NAME: "analysis_run",
                cs.KEY_PROJECT_NAME: self.project_name,
                cs.KEY_ANALYSIS_RUN_ID: run_id,
                cs.KEY_ANALYSIS_TIMESTAMP: timestamp,
                cs.KEY_GIT_HEAD: git_head,
            },
        )
        self.ingestor.ensure_relationship_batch(
            (cs.NodeLabel.PROJECT, cs.KEY_NAME, self.project_name),
            cs.RelationshipType.HAS_RUN,
            (cs.NodeLabel.ANALYSIS_RUN, cs.KEY_QUALIFIED_NAME, run_qn),
        )

        self.ingestor.ensure_node_batch(
            cs.NodeLabel.ANALYSIS_REPORT,
            {
                cs.KEY_QUALIFIED_NAME: report_qn,
                cs.KEY_NAME: "analysis_report",
                cs.KEY_PROJECT_NAME: self.project_name,
                cs.KEY_ANALYSIS_RUN_ID: run_id,
                cs.KEY_ANALYSIS_TIMESTAMP: timestamp,
                cs.KEY_ANALYSIS_SUMMARY: json.dumps(summary, ensure_ascii=False),
                cs.KEY_GIT_HEAD: git_head,
            },
        )
        self.ingestor.ensure_relationship_batch(
            (cs.NodeLabel.PROJECT, cs.KEY_NAME, self.project_name),
            cs.RelationshipType.HAS_ANALYSIS,
            (cs.NodeLabel.ANALYSIS_REPORT, cs.KEY_QUALIFIED_NAME, report_qn),
        )
        self.ingestor.ensure_relationship_batch(
            (cs.NodeLabel.ANALYSIS_RUN, cs.KEY_QUALIFIED_NAME, run_qn),
            cs.RelationshipType.HAS_ANALYSIS,
            (cs.NodeLabel.ANALYSIS_REPORT, cs.KEY_QUALIFIED_NAME, report_qn),
        )

        for metric_name, metric_value in summary.items():
            metric_qn = f"{report_qn}{cs.SEPARATOR_DOT}metric.{metric_name}"
            self.ingestor.ensure_node_batch(
                cs.NodeLabel.ANALYSIS_METRIC,
                {
                    cs.KEY_QUALIFIED_NAME: metric_qn,
                    cs.KEY_NAME: metric_name,
                    cs.KEY_METRIC_NAME: metric_name,
                    cs.KEY_METRIC_VALUE: json.dumps(metric_value, ensure_ascii=False),
                    cs.KEY_ANALYSIS_RUN_ID: run_id,
                    cs.KEY_ANALYSIS_TIMESTAMP: timestamp,
                    cs.KEY_PROJECT_NAME: self.project_name,
                },
            )
            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.ANALYSIS_REPORT, cs.KEY_QUALIFIED_NAME, report_qn),
                cs.RelationshipType.HAS_METRIC,
                (cs.NodeLabel.ANALYSIS_METRIC, cs.KEY_QUALIFIED_NAME, metric_qn),
            )

    def _primary_label(self, node: NodeRecord) -> str:
        for label in (
            cs.NodeLabel.FUNCTION,
            cs.NodeLabel.METHOD,
            cs.NodeLabel.CLASS,
            cs.NodeLabel.MODULE,
        ):
            if label.value in node.labels:
                return str(label.value)
        return node.labels[0] if node.labels else ""
