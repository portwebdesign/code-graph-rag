from __future__ import annotations

import json
import os
import re
from pathlib import Path

from codebase_rag.core import constants as cs

from ..protocols import AnalysisRunnerProtocol
from ..types import NodeRecord, RelationshipRecord


class SecurityAuditMixin:
    def _sast_taint_tracking(
        self: AnalysisRunnerProtocol, nodes: list[NodeRecord]
    ) -> dict[str, int]:
        file_paths = self._collect_file_paths(nodes)

        sources = [
            r"request\.get",
            r"request\.post",
            r"input\(",
            r"os\.environ",
            r"sys\.argv",
        ]
        sinks = [
            r"eval\(",
            r"exec\(",
            r"os\.system\(",
            r"subprocess\.run\(",
            r"cursor\.execute\(",
        ]

        findings: list[dict[str, object]] = []

        for path in file_paths:
            file_path = self.repo_path / path
            if not file_path.exists() or file_path.stat().st_size > 1_000_000:
                continue
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if any(re.search(pattern, content) for pattern in sources) and any(
                re.search(pattern, content) for pattern in sinks
            ):
                findings.append({"path": path, "risk": "source_to_sink"})

        self._write_json_report("taint_report.json", findings)
        return {"findings": len(findings)}

    def _license_compliance(self: AnalysisRunnerProtocol) -> dict[str, int]:
        deps: set[str] = set()
        pyproject = self.repo_path / "pyproject.toml"
        package_json = self.repo_path / "package.json"

        if pyproject.exists():
            text = pyproject.read_text(encoding="utf-8", errors="ignore")
            deps.update(re.findall(r"\"([a-zA-Z0-9_.-]+)\"\s*>=", text))
        if package_json.exists():
            text = package_json.read_text(encoding="utf-8", errors="ignore")
            deps.update(re.findall(r"\"([@a-zA-Z0-9_./-]+)\"\s*:", text))

        license_file = self.repo_path / "LICENSE"
        project_license = "unknown"
        if license_file.exists():
            head = license_file.read_text(encoding="utf-8", errors="ignore")[
                :200
            ].lower()
            if "mit" in head:
                project_license = "MIT"
            elif "apache" in head:
                project_license = "Apache"
            elif "gpl" in head:
                project_license = "GPL"

        report_payload = {
            "project_license": project_license,
            "dependencies_count": len(deps),
            "dependencies": sorted(deps)[:200],
            "unknown_dependency_licenses": len(deps),
        }
        self._write_json_report("license_report.json", report_payload)
        return {"dependencies": len(deps)}

    def _arch_drift(
        self: AnalysisRunnerProtocol,
        nodes: list[NodeRecord],
        relationships: list[RelationshipRecord],
        node_by_id: dict[int, NodeRecord],
    ) -> dict[str, int]:
        config_path = os.getenv("CODEGRAPH_ARCH_DRIFT_CONFIG", "")
        if not config_path:
            return {"configured": 0}

        config_file = Path(config_path)
        if not config_file.is_file():
            return {"configured": 0}

        try:
            config = json.loads(config_file.read_text(encoding="utf-8"))
        except Exception:
            return {"configured": 0}

        layers = config.get("layers", [])
        if not layers:
            return {"configured": 0}

        layer_order = {layer: idx for idx, layer in enumerate(layers)}

        def layer_for(path: str) -> str | None:
            parts = path.lower().split("/")
            for layer in layers:
                if layer in parts:
                    return layer
            return None

        module_nodes = {
            node.node_id: node
            for node in nodes
            if cs.NodeLabel.MODULE.value in node.labels
        }

        violations: list[dict[str, object]] = []
        for rel in relationships:
            if rel.rel_type not in {
                cs.RelationshipType.IMPORTS,
                cs.RelationshipType.RESOLVES_IMPORT,
            }:
                continue
            source = module_nodes.get(rel.from_id)
            target = module_nodes.get(rel.to_id)
            if not source or not target:
                continue
            src_path = str(source.properties.get(cs.KEY_PATH) or "")
            tgt_path = str(target.properties.get(cs.KEY_PATH) or "")
            src_layer = layer_for(src_path)
            tgt_layer = layer_for(tgt_path)
            if (
                src_layer
                and tgt_layer
                and layer_order[src_layer] < layer_order[tgt_layer]
            ):
                violations.append(
                    {
                        "from": source.properties.get(cs.KEY_QUALIFIED_NAME),
                        "to": target.properties.get(cs.KEY_QUALIFIED_NAME),
                        "from_layer": src_layer,
                        "to_layer": tgt_layer,
                    }
                )

        self._write_json_report("arch_drift_report.json", violations)
        return {"violations": len(violations), "configured": 1}
