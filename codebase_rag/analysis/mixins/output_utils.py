from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..protocols import AnalysisRunnerProtocol


class OutputUtilsMixin:
    def _analysis_output_dir(self: AnalysisRunnerProtocol) -> Path:
        output_dir = self.repo_path / "output" / "analysis"
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def _write_json_report(
        self: AnalysisRunnerProtocol, filename: str, payload: Any
    ) -> Path:
        report_path = self._analysis_output_dir() / filename
        report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return report_path

    def _write_text_report(
        self: AnalysisRunnerProtocol, filename: str, content: str
    ) -> Path:
        report_path = self._analysis_output_dir() / filename
        report_path.write_text(content, encoding="utf-8")
        return report_path
