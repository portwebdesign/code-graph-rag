from __future__ import annotations

from typing import Any

from codebase_rag.core import constants as cs

from .base_module import AnalysisContext, AnalysisModule


class DocumentationQualityModule(AnalysisModule):
    def get_name(self) -> str:
        return "documentation_quality"

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        if not context.nodes:
            return {}

        symbols = [
            node
            for node in context.nodes
            if any(
                label in node.labels
                for label in (
                    cs.NodeLabel.FUNCTION.value,
                    cs.NodeLabel.METHOD.value,
                    cs.NodeLabel.CLASS.value,
                )
            )
        ]
        total = len(symbols)
        missing: list[dict[str, object]] = []

        for node in symbols:
            docstring = node.properties.get(cs.KEY_DOCSTRING)
            if not isinstance(docstring, str) or not docstring.strip():
                missing.append(
                    {
                        "qualified_name": node.properties.get(cs.KEY_QUALIFIED_NAME),
                        "path": node.properties.get(cs.KEY_PATH),
                        "name": node.properties.get(cs.KEY_NAME),
                    }
                )

        coverage = 0.0
        if total:
            coverage = (total - len(missing)) / total

        low_comment_files = self._low_comment_files(context)
        missing_by_file: dict[str, int] = {}
        for entry in missing:
            path = str(entry.get("path") or "")
            if not path:
                continue
            missing_by_file[path] = missing_by_file.get(path, 0) + 1
        top_missing = sorted(
            (
                {"path": path, "missing_docstrings": count}
                for path, count in missing_by_file.items()
            ),
            key=lambda item: item["missing_docstrings"],
            reverse=True,
        )
        readme_summary = self._readme_summary(context)

        report = {
            "missing_docstrings": missing,
            "low_comment_files": low_comment_files,
            "missing_by_file": top_missing,
            "readme": readme_summary,
        }
        context.runner._write_json_report("documentation_quality_report.json", report)

        return {
            "total_symbols": total,
            "missing_docstrings": len(missing),
            "docstring_coverage": round(coverage, 4),
            "low_comment_files": low_comment_files[:50],
            "low_comment_files_count": len(low_comment_files),
            "top_missing": top_missing[:20],
            "readme": readme_summary,
        }

    @staticmethod
    def _low_comment_files(context: AnalysisContext) -> list[dict[str, object]]:
        paths = list({path for path in context.module_path_map.values() if path})
        results: list[dict[str, object]] = []
        for path in paths[:200]:
            file_path = context.runner.repo_path / path
            if not file_path.exists():
                continue
            try:
                content = file_path.read_text(
                    encoding=cs.ENCODING_UTF8, errors="ignore"
                )
            except Exception:
                continue
            ratio = DocumentationQualityModule._comment_ratio(content)
            if ratio < 0.05:
                results.append({"path": path, "comment_ratio": round(ratio, 4)})
        return results

    @staticmethod
    def _comment_ratio(content: str) -> float:
        in_block = False
        comment_lines = 0
        code_lines = 0

        for raw in content.splitlines():
            line = raw.strip()
            if not line:
                continue
            if in_block:
                comment_lines += 1
                if "*/" in line:
                    in_block = False
                continue
            if line.startswith("/*"):
                comment_lines += 1
                if "*/" not in line:
                    in_block = True
                continue
            if line.startswith(("#", "//", "--", "*")):
                comment_lines += 1
                continue
            code_lines += 1

        total = comment_lines + code_lines
        if not total:
            return 0.0
        return comment_lines / total

    @staticmethod
    def _readme_summary(context: AnalysisContext) -> dict[str, object]:
        repo_path = context.runner.repo_path
        candidates = [
            repo_path / "README.md",
            repo_path / "README.MD",
            repo_path / "readme.md",
            repo_path / "README",
        ]
        for path in candidates:
            if path.exists():
                try:
                    content = path.read_text(encoding=cs.ENCODING_UTF8, errors="ignore")
                except Exception:
                    return {"present": True, "words": 0, "path": str(path)}
                words = [word for word in content.split() if word.strip()]
                return {
                    "present": True,
                    "words": len(words),
                    "path": str(path.relative_to(repo_path)),
                }
        return {"present": False, "words": 0, "path": None}
