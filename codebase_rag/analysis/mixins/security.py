from __future__ import annotations

import json

from codebase_rag.core import constants as cs

from ...security.security_scanner import SecurityScanner
from ..protocols import AnalysisRunnerProtocol
from ..types import NodeRecord


class SecurityMixin:
    def _security_scan(
        self: AnalysisRunnerProtocol, nodes: list[NodeRecord]
    ) -> dict[str, int]:
        file_paths = [
            node.properties.get(cs.KEY_PATH)
            for node in nodes
            if cs.NodeLabel.FILE.value in node.labels
        ]
        file_paths = [str(path) for path in file_paths if isinstance(path, str)]

        scanner = SecurityScanner()
        findings = scanner.scan_files(self.repo_path / path for path in file_paths)

        output_dir = self.repo_path / "output" / "analysis"
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "security_report.json"
        report_path.write_text(
            json.dumps([finding.__dict__ for finding in findings], indent=2),
            encoding="utf-8",
        )
        return {"findings": len(findings)}
