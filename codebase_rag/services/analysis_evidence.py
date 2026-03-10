from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlparse

from codebase_rag.core import constants as cs


class AnalysisEvidenceService:
    _ARTIFACT_URI_PREFIX = "analysis://artifact/"
    _BUNDLE_URI_PREFIX = "analysis://bundle/"
    _MANIFEST_URI = "analysis://manifest"
    _OVERVIEW_URI = "analysis://overview"
    _BUNDLE_ARTIFACT_LIMIT = 8
    _TRUSTED_FINDING_LIMIT = 12
    _IGNORED_FINDING_LIMIT = 8
    _SUMMARY_KEY_BLACKLIST = {"summary", "reason", "metadata", "ui_summary"}
    _FINDING_KEYS = (
        "violations",
        "findings",
        "issues",
        "top_issues",
        "top_violations",
        "chains",
        "cycles",
        "duplicates",
        "candidates",
        "results",
        "entries",
        "endpoints",
        "hotspots",
        "dependencies",
        "artifacts",
    )
    _IGNORE_PATH_MARKERS = {
        ".git",
        ".idea",
        ".next",
        ".venv",
        ".yarn",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "generated",
        "node_modules",
        "site-packages",
        "vendor",
        "venv",
    }
    _TEST_PATH_MARKERS = {
        "spec",
        "tests",
        "test",
        "testdata",
        "__tests__",
    }
    _BUNDLE_TO_PROMPT = {
        "analysis_bundle_for_goal": "analysis_overview",
        "architecture_bundle": "architecture_review",
        "change_bundle": "change_review",
        "risk_bundle": "risk_review",
        "test_bundle": "test_review",
    }

    def __init__(self, repo_path: str | Path) -> None:
        self.repo_path = Path(repo_path).resolve()

    def list_artifacts(self) -> dict[str, object]:
        artifacts = [self._artifact_metadata(path) for path in self._artifact_paths()]
        return {
            "count": len(artifacts),
            "artifacts": artifacts,
            "resources": self.list_resources(),
            "overview": self._build_overview(artifacts),
        }

    def get_artifact(self, artifact_name: str) -> dict[str, object]:
        report_path = self._resolve_artifact_path(artifact_name)
        if report_path is None:
            available = [path.name for path in self._artifact_paths()]
            return {"error": "artifact_not_found", "available_artifacts": available}

        parsed_payload = self._parse_artifact(report_path)
        normalized = self._normalize_artifact(report_path, parsed_payload)
        return {
            "artifact": report_path.stem,
            "filename": report_path.name,
            "content": report_path.read_text(encoding=cs.ENCODING_UTF8),
            "normalized": normalized,
        }

    def list_resources(self) -> list[dict[str, object]]:
        resources: list[dict[str, object]] = [
            {
                "uri": self._MANIFEST_URI,
                "name": "analysis_manifest",
                "description": "Normalized manifest of output/analysis artifacts.",
                "mime_type": "application/json",
            },
            {
                "uri": self._OVERVIEW_URI,
                "name": "analysis_overview",
                "description": "High-level overview of normalized analysis evidence.",
                "mime_type": "application/json",
            },
        ]

        for bundle_name in self._BUNDLE_TO_PROMPT:
            resources.append(
                {
                    "uri": f"{self._BUNDLE_URI_PREFIX}{bundle_name}",
                    "name": bundle_name,
                    "description": f"Normalized evidence bundle for {bundle_name}.",
                    "mime_type": "application/json",
                }
            )

        for path in self._artifact_paths():
            metadata = self._artifact_metadata(path)
            resources.append(
                {
                    "uri": f"{self._ARTIFACT_URI_PREFIX}{path.name}",
                    "name": path.stem,
                    "description": str(metadata.get("summary", "")).strip()
                    or f"Normalized analysis artifact for {path.name}.",
                    "mime_type": "application/json",
                }
            )
        return resources

    def read_resource(
        self,
        uri: str,
        *,
        session_state: dict[str, object] | None = None,
    ) -> dict[str, object]:
        parsed = urlparse(uri)
        normalized_uri = str(uri).strip()
        if normalized_uri == self._MANIFEST_URI:
            return self.list_artifacts()
        if normalized_uri == self._OVERVIEW_URI:
            return self._build_overview(
                [self._artifact_metadata(path) for path in self._artifact_paths()]
            )

        if normalized_uri.startswith(self._ARTIFACT_URI_PREFIX):
            artifact_name = normalized_uri.removeprefix(self._ARTIFACT_URI_PREFIX)
            return self.get_artifact(artifact_name)

        if normalized_uri.startswith(self._BUNDLE_URI_PREFIX):
            bundle_name = normalized_uri.removeprefix(self._BUNDLE_URI_PREFIX)
            arguments = {
                key: values[0]
                for key, values in parse_qs(parsed.query).items()
                if values
            }
            return self.build_bundle(
                bundle_name,
                session_state=session_state,
                **arguments,
            )

        return {"error": "resource_not_found", "uri": normalized_uri}

    def list_prompts(self) -> list[dict[str, object]]:
        return [
            {
                "name": prompt_name,
                "description": self._prompt_description(prompt_name),
                "arguments": [
                    {
                        "name": "goal",
                        "description": "Optional task or question to focus the bundle.",
                        "required": False,
                    },
                    {
                        "name": "context",
                        "description": "Optional task context to bias artifact selection.",
                        "required": False,
                    },
                    {
                        "name": "qualified_name",
                        "description": "Optional symbol target for change/risk bundles.",
                        "required": False,
                    },
                    {
                        "name": "file_path",
                        "description": "Optional file target for change/risk bundles.",
                        "required": False,
                    },
                ],
            }
            for prompt_name in self._BUNDLE_TO_PROMPT.values()
        ]

    def get_prompt(
        self,
        prompt_name: str,
        arguments: dict[str, str] | None = None,
        *,
        session_state: dict[str, object] | None = None,
    ) -> dict[str, object]:
        normalized_arguments = {
            key: str(value).strip()
            for key, value in cast(dict[str, object], arguments or {}).items()
            if str(value).strip()
        }
        bundle_name = next(
            (
                candidate_bundle
                for candidate_bundle, candidate_prompt in self._BUNDLE_TO_PROMPT.items()
                if candidate_prompt == prompt_name
            ),
            None,
        )
        if bundle_name is None:
            return {"error": "prompt_not_found", "name": prompt_name}

        bundle = self.build_bundle(
            bundle_name,
            session_state=session_state,
            **normalized_arguments,
        )
        bundle_uri = self._bundle_uri(bundle_name, normalized_arguments)
        text = "\n".join(
            [
                f"Prompt: {prompt_name}",
                f"Focus bundle: {bundle_uri}",
                "Use normalized bundle findings, not raw file dumps.",
                "Prefer the bundle summary, trusted_findings, topology signals, and exact_next_calls.",
                "Recommended resources:",
                f"- {bundle_uri}",
                f"- {self._OVERVIEW_URI}",
                "Bundle excerpt:",
                json.dumps(bundle, indent=2, ensure_ascii=False)[:4000],
            ]
        ).strip()
        return {
            "name": prompt_name,
            "description": self._prompt_description(prompt_name),
            "messages": [
                {
                    "role": "user",
                    "text": text,
                }
            ],
        }

    def build_bundle(
        self,
        bundle_name: str,
        *,
        goal: str | None = None,
        context: str | None = None,
        qualified_name: str | None = None,
        file_path: str | None = None,
        session_state: dict[str, object] | None = None,
    ) -> dict[str, object]:
        artifacts = [self._artifact_metadata(path) for path in self._artifact_paths()]
        selected = self._select_bundle_artifacts(
            bundle_name,
            artifacts=artifacts,
            goal=goal,
            context=context,
            qualified_name=qualified_name,
            file_path=file_path,
        )
        selected = selected[: self._BUNDLE_ARTIFACT_LIMIT]
        session_payload = self._bundle_session_payload(session_state or {})

        key_findings: list[dict[str, object]] = []
        ignored_paths: set[str] = set()
        resource_uris: list[str] = []
        for artifact in selected:
            resource_uri = str(artifact.get("resource_uri", "")).strip()
            if resource_uri:
                resource_uris.append(resource_uri)
            for finding in cast(
                list[dict[str, object]], artifact.get("trusted_findings", [])
            ):
                key_findings.append(finding)
            for path in cast(list[str], artifact.get("ignored_paths", [])):
                ignored_paths.add(path)

        exact_next_calls = self._bundle_next_calls(
            bundle_name=bundle_name,
            goal=goal,
            context=context,
            qualified_name=qualified_name,
            file_path=file_path,
        )
        summary = self._bundle_summary(
            bundle_name=bundle_name,
            selected=selected,
            session_payload=session_payload,
            goal=goal,
            context=context,
            qualified_name=qualified_name,
            file_path=file_path,
        )
        return {
            "bundle": bundle_name,
            "bundle_uri": self._bundle_uri(
                bundle_name,
                {
                    key: value
                    for key, value in {
                        "goal": goal,
                        "context": context,
                        "qualified_name": qualified_name,
                        "file_path": file_path,
                    }.items()
                    if isinstance(value, str) and value.strip()
                },
            ),
            "summary": summary,
            "goal": str(goal or "").strip(),
            "context": str(context or "").strip(),
            "target": {
                "qualified_name": str(qualified_name or "").strip(),
                "file_path": str(file_path or "").strip(),
            },
            "artifacts": selected,
            "key_findings": key_findings[:20],
            "ignored_paths": sorted(ignored_paths),
            "session_evidence": session_payload,
            "resource_uris": resource_uris,
            "next_best_action": exact_next_calls[0] if exact_next_calls else {},
            "exact_next_calls": exact_next_calls,
        }

    def _analysis_dir(self) -> Path:
        return self.repo_path / "output" / "analysis"

    def _artifact_paths(self) -> list[Path]:
        analysis_dir = self._analysis_dir()
        if not analysis_dir.exists() or not analysis_dir.is_dir():
            return []
        return sorted(
            [
                path
                for path in analysis_dir.glob("*")
                if path.is_file() and path.name != "analysis_manifest.json"
            ],
            key=lambda item: item.name,
        )

    def _resolve_artifact_path(self, artifact_name: str) -> Path | None:
        normalized_name = artifact_name.strip()
        if not normalized_name:
            return None

        request_path = Path(normalized_name)
        if request_path.is_absolute() or any(
            part in {"..", "."} for part in request_path.parts
        ):
            return None

        if request_path.suffix:
            candidate_paths = [(self._analysis_dir() / request_path).resolve()]
        else:
            candidate_paths = [
                (self._analysis_dir() / f"{normalized_name}{suffix}").resolve()
                for suffix in (".json", ".md", ".log")
            ]

        analysis_dir = self._analysis_dir().resolve()
        return next(
            (
                candidate
                for candidate in candidate_paths
                if candidate.parent == analysis_dir
                and candidate.exists()
                and candidate.is_file()
            ),
            None,
        )

    def _parse_artifact(self, report_path: Path) -> object:
        content = report_path.read_text(encoding=cs.ENCODING_UTF8)
        if report_path.suffix.lower() == ".json":
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return {"raw_text": content}
        return content

    def _artifact_metadata(self, report_path: Path) -> dict[str, object]:
        stat = report_path.stat()
        parsed_payload = self._parse_artifact(report_path)
        normalized = self._normalize_artifact(report_path, parsed_payload)
        return {
            "name": report_path.name,
            "stem": report_path.stem,
            "extension": report_path.suffix,
            "size_bytes": stat.st_size,
            "modified_at": int(stat.st_mtime),
            **normalized,
        }

    def _normalize_artifact(
        self,
        report_path: Path,
        payload: object,
    ) -> dict[str, object]:
        raw_findings = self._extract_findings(payload)
        trusted_findings: list[dict[str, object]] = []
        ignored_findings: list[dict[str, object]] = []
        ignored_paths: set[str] = set()

        for finding in raw_findings:
            classification = self._classify_finding_paths(finding)
            normalized = {
                "category": str(finding.get("category", "")).strip(),
                "summary": self._finding_summary(finding),
                "paths": classification["paths"],
                "flags": classification["flags"],
                "raw": finding.get("raw"),
            }
            if classification["ignored"]:
                ignored_findings.append(normalized)
                ignored_paths.update(cast(list[str], classification["paths"]))
            else:
                trusted_findings.append(normalized)

        confidence = self._artifact_confidence(
            report_path,
            trusted_count=len(trusted_findings),
            ignored_count=len(ignored_findings),
            payload=payload,
        )
        kind = self._infer_artifact_kind(report_path.stem, payload)
        freshness = self._freshness_payload(report_path.stat().st_mtime)
        summary = self._artifact_summary(
            report_path=report_path,
            kind=kind,
            payload=payload,
            trusted_count=len(trusted_findings),
            ignored_count=len(ignored_findings),
        )
        next_actions = self._artifact_next_actions(
            report_path.stem,
            kind=kind,
            trusted_count=len(trusted_findings),
        )
        metrics = self._extract_metrics(payload)

        return {
            "kind": kind,
            "summary": summary,
            "trusted_findings": trusted_findings[: self._TRUSTED_FINDING_LIMIT],
            "ignored_findings": ignored_findings[: self._IGNORED_FINDING_LIMIT],
            "ignored_paths": sorted(ignored_paths),
            "confidence": confidence,
            "freshness": freshness,
            "next_actions": next_actions,
            "metrics": metrics,
            "resource_uri": f"{self._ARTIFACT_URI_PREFIX}{report_path.name}",
        }

    def _extract_findings(self, payload: object) -> list[dict[str, object]]:
        if isinstance(payload, list):
            return [
                {"category": "items", "raw": item}
                for item in cast(list[object], payload)
            ]

        if isinstance(payload, str):
            lines = [line.strip() for line in payload.splitlines() if line.strip()]
            return [{"category": "lines", "raw": line} for line in lines[:50]]

        if not isinstance(payload, dict):
            return []

        findings: list[dict[str, object]] = []
        payload_dict = cast(dict[str, object], payload)
        for key in self._FINDING_KEYS:
            value = payload_dict.get(key)
            if isinstance(value, list):
                findings.extend({"category": key, "raw": item} for item in value)

        for key, value in payload_dict.items():
            if key in self._SUMMARY_KEY_BLACKLIST or key in self._FINDING_KEYS:
                continue
            if isinstance(value, list):
                items = cast(list[object], value)
                findings.extend({"category": key, "raw": item} for item in items[:50])
        return findings

    def _extract_metrics(self, payload: object) -> dict[str, object]:
        if isinstance(payload, list):
            return {"items": len(payload)}
        if not isinstance(payload, dict):
            return {}
        payload_dict = cast(dict[str, object], payload)
        metrics: dict[str, object] = {}
        for key, value in payload_dict.items():
            if isinstance(value, int | float | str | bool) and key not in {"reason"}:
                metrics[key] = value
            elif isinstance(value, dict):
                nested = cast(dict[str, object], value)
                if all(
                    isinstance(item, int | float | str | bool)
                    for item in nested.values()
                ):
                    metrics[key] = nested
        return metrics

    def _classify_finding_paths(self, finding: dict[str, object]) -> dict[str, object]:
        raw = finding.get("raw")
        paths = self._extract_paths(raw)
        flags: list[str] = []
        ignored = False
        for path in paths:
            lowered = path.lower()
            parts = set(lowered.replace("\\", "/").split("/"))
            if not self._IGNORE_PATH_MARKERS.isdisjoint(parts):
                flags.append("vendor_or_generated")
                ignored = True
            if not self._TEST_PATH_MARKERS.isdisjoint(parts):
                flags.append("test_path")
                ignored = True
        return {
            "paths": sorted(set(paths)),
            "flags": sorted(set(flags)),
            "ignored": ignored,
        }

    def _extract_paths(self, raw: object) -> list[str]:
        matches: set[str] = set()

        def _visit(value: object) -> None:
            if isinstance(value, dict):
                for key, nested in cast(dict[str, object], value).items():
                    if key in {"path", "file", "file_path", "module_path"}:
                        candidate = str(nested).strip()
                        if candidate:
                            matches.add(candidate.replace("\\", "/"))
                    else:
                        _visit(nested)
            elif isinstance(value, list):
                for item in cast(list[object], value):
                    _visit(item)
            elif isinstance(value, str):
                if "/" in value or "\\" in value:
                    normalized = value.strip().replace("\\", "/")
                    if "." in Path(normalized).name or "/" in normalized:
                        matches.add(normalized)

        _visit(raw)
        return sorted(matches)

    @staticmethod
    def _finding_summary(finding: dict[str, object]) -> str:
        raw = finding.get("raw")
        if isinstance(raw, dict):
            payload = cast(dict[str, object], raw)
            for key in ("qualified_name", "name", "path", "file", "type"):
                candidate = str(payload.get(key, "")).strip()
                if candidate:
                    return candidate
            return json.dumps(payload, ensure_ascii=False)[:240]
        if isinstance(raw, str):
            return raw[:240]
        return str(raw)[:240]

    def _artifact_confidence(
        self,
        report_path: Path,
        *,
        trusted_count: int,
        ignored_count: int,
        payload: object,
    ) -> float:
        confidence = 0.7
        if report_path.suffix.lower() == ".json":
            confidence += 0.15
        elif report_path.suffix.lower() == ".md":
            confidence += 0.1
        if isinstance(payload, dict) and payload:
            confidence += 0.05
        total = trusted_count + ignored_count
        if total > 0:
            confidence += min(0.1, trusted_count / total * 0.1)
            confidence -= min(0.2, ignored_count / total * 0.2)
        return round(max(0.0, min(confidence, 0.98)), 3)

    def _artifact_summary(
        self,
        *,
        report_path: Path,
        kind: str,
        payload: object,
        trusted_count: int,
        ignored_count: int,
    ) -> str:
        parts = [f"{report_path.stem} ({kind})"]
        if trusted_count > 0:
            parts.append(f"trusted_findings={trusted_count}")
        if ignored_count > 0:
            parts.append(f"ignored_findings={ignored_count}")
        if isinstance(payload, dict):
            summary_block = cast(dict[str, object], payload).get("summary")
            if isinstance(summary_block, dict):
                summary_pairs = []
                for key, value in cast(dict[str, object], summary_block).items():
                    if isinstance(value, int | float | str):
                        summary_pairs.append(f"{key}={value}")
                if summary_pairs:
                    parts.append(", ".join(summary_pairs[:4]))
        return " | ".join(parts)

    @staticmethod
    def _infer_artifact_kind(stem: str, payload: object) -> str:
        lowered = stem.lower()
        if "security" in lowered or "secret" in lowered or "taint" in lowered:
            return "security"
        if "perf" in lowered or "hotspot" in lowered:
            return "performance"
        if "dependency" in lowered:
            return "dependency"
        if "api" in lowered or "endpoint" in lowered:
            return "api"
        if "test" in lowered or "coverage" in lowered:
            return "test"
        if "blast" in lowered or "fan" in lowered or "cycle" in lowered:
            return "topology"
        if "migration" in lowered:
            return "migration"
        if isinstance(payload, str):
            return "narrative"
        return "analysis"

    def _artifact_next_actions(
        self,
        stem: str,
        *,
        kind: str,
        trusted_count: int,
    ) -> list[str]:
        if trusted_count <= 0:
            return ["run_analysis", "query_code_graph"]
        if kind == "security":
            return ["risk_bundle", "query_code_graph", "read_file"]
        if kind == "api":
            return ["architecture_bundle", "multi_hop_analysis", "read_file"]
        if kind == "topology":
            return ["architecture_bundle", "impact_graph"]
        if kind == "test":
            return ["test_bundle", "test_generate"]
        if "migration" in stem.lower():
            return ["analysis_bundle_for_goal", "plan_task"]
        return ["analysis_bundle_for_goal", "query_code_graph"]

    @staticmethod
    def _freshness_payload(modified_at: float) -> dict[str, object]:
        age_seconds = max(0, int(time.time() - modified_at))
        return {
            "modified_at": int(modified_at),
            "age_seconds": age_seconds,
            "stale": age_seconds > 24 * 60 * 60,
        }

    def _build_overview(self, artifacts: list[dict[str, object]]) -> dict[str, object]:
        by_kind: dict[str, int] = {}
        for artifact in artifacts:
            kind = str(artifact.get("kind", "analysis")).strip() or "analysis"
            by_kind[kind] = by_kind.get(kind, 0) + 1
        return {
            "artifact_count": len(artifacts),
            "kinds": by_kind,
            "top_artifacts": artifacts[:10],
        }

    def _select_bundle_artifacts(
        self,
        bundle_name: str,
        *,
        artifacts: list[dict[str, object]],
        goal: str | None,
        context: str | None,
        qualified_name: str | None,
        file_path: str | None,
    ) -> list[dict[str, object]]:
        bundle_priorities = {
            "analysis_bundle_for_goal": self._artifact_keywords(goal, context),
            "architecture_bundle": {
                "api",
                "architecture",
                "dependency",
                "topology",
                "docker",
                "graphql",
                "redis",
                "postgres",
                "memgraph",
            },
            "change_bundle": {
                "change",
                "impact",
                "blast",
                "dependency",
                "api",
            },
            "risk_bundle": {
                "risk",
                "security",
                "secret",
                "taint",
                "performance",
                "dependency",
            },
            "test_bundle": {
                "test",
                "coverage",
                "public_api",
                "blast",
                "impact",
            },
        }
        keywords = bundle_priorities.get(bundle_name, set())
        if qualified_name:
            keywords.update(self._artifact_keywords(qualified_name))
        if file_path:
            keywords.update(self._artifact_keywords(file_path))

        scored: list[tuple[int, dict[str, object]]] = []
        for artifact in artifacts:
            score = 0
            haystack = " ".join(
                [
                    str(artifact.get("name", "")),
                    str(artifact.get("kind", "")),
                    str(artifact.get("summary", "")),
                ]
            ).lower()
            for keyword in keywords:
                if keyword and keyword in haystack:
                    score += 2
            for finding in cast(
                list[dict[str, object]], artifact.get("trusted_findings", [])
            )[:3]:
                summary = str(finding.get("summary", "")).lower()
                for keyword in keywords:
                    if keyword and keyword in summary:
                        score += 1
            scored.append((score, artifact))

        scored.sort(
            key=lambda item: (
                item[0],
                self._coerce_int(item[1].get("modified_at", 0)),
                self._coerce_int(item[1].get("size_bytes", 0)),
            ),
            reverse=True,
        )
        selected = [artifact for score, artifact in scored if score > 0]
        if selected:
            return selected

        if bundle_name == "architecture_bundle":
            preferred_kinds = {"api", "topology", "dependency"}
            return [
                artifact
                for artifact in artifacts
                if str(artifact.get("kind", "")) in preferred_kinds
            ]
        if bundle_name == "risk_bundle":
            preferred_kinds = {"security", "performance", "dependency"}
            return [
                artifact
                for artifact in artifacts
                if str(artifact.get("kind", "")) in preferred_kinds
            ]
        if bundle_name == "test_bundle":
            preferred_kinds = {"test", "topology"}
            return [
                artifact
                for artifact in artifacts
                if str(artifact.get("kind", "")) in preferred_kinds
            ]
        return artifacts[: self._BUNDLE_ARTIFACT_LIMIT]

    @staticmethod
    def _artifact_keywords(*texts: str | None) -> set[str]:
        tokens: set[str] = set()
        for text in texts:
            normalized = str(text or "").strip().lower()
            if not normalized:
                continue
            for token in re.split(r"[^a-z0-9_/\\-]+", normalized):
                if len(token) >= 3:
                    tokens.add(token)
        return tokens

    @staticmethod
    def _bundle_session_payload(session_state: dict[str, object]) -> dict[str, object]:
        return {
            "last_analysis_bundle": session_state.get("last_analysis_bundle", {}),
            "last_architecture_bundle": session_state.get(
                "last_architecture_bundle", {}
            ),
            "last_change_bundle": session_state.get("last_change_bundle", {}),
            "last_impact_bundle": session_state.get("last_impact_bundle", {}),
            "last_multi_hop_bundle": session_state.get("last_multi_hop_bundle", {}),
            "last_risk_bundle": session_state.get("last_risk_bundle", {}),
            "last_test_bundle": session_state.get("last_test_bundle", {}),
            "last_test_selection": session_state.get("last_test_selection", {}),
            "repo_semantics": session_state.get("repo_semantics", {}),
            "graph_dirty": bool(session_state.get("graph_dirty", False)),
        }

    def _bundle_summary(
        self,
        *,
        bundle_name: str,
        selected: list[dict[str, object]],
        session_payload: dict[str, object],
        goal: str | None,
        context: str | None,
        qualified_name: str | None,
        file_path: str | None,
    ) -> str:
        focus_parts = [
            value
            for value in [goal, context, qualified_name, file_path]
            if isinstance(value, str) and value.strip()
        ]
        summary = f"{bundle_name} with {len(selected)} normalized artifacts"
        if focus_parts:
            summary += " | focus=" + " | ".join(focus_parts[:3])
        repo_semantics = session_payload.get("repo_semantics", {})
        if isinstance(repo_semantics, dict):
            repo_semantics_dict = cast(dict[str, object], repo_semantics)
            semantics_summary = str(repo_semantics_dict.get("summary", "")).strip()
            if semantics_summary:
                summary += " | repo_semantics=" + semantics_summary
        return summary

    def _bundle_next_calls(
        self,
        *,
        bundle_name: str,
        goal: str | None,
        context: str | None,
        qualified_name: str | None,
        file_path: str | None,
    ) -> list[dict[str, object]]:
        focus_text = str(goal or context or qualified_name or file_path or "").strip()
        if bundle_name == "architecture_bundle":
            return [
                {
                    "tool": cs.MCPToolName.MULTI_HOP_ANALYSIS,
                    "copy_paste": (
                        f'{cs.MCPToolName.MULTI_HOP_ANALYSIS}(qualified_name="{qualified_name}", depth=3)'
                        if qualified_name
                        else (
                            f'{cs.MCPToolName.MULTI_HOP_ANALYSIS}(file_path="{file_path}", depth=3)'
                            if file_path
                            else f'{cs.MCPToolName.QUERY_CODE_GRAPH}(natural_language_query="map architecture, services, endpoints, data stores")'
                        )
                    ),
                    "why": "expand architecture evidence with typed graph traversal",
                    "when": "after reviewing the architecture bundle",
                }
            ]
        if bundle_name == "change_bundle":
            return [
                {
                    "tool": cs.MCPToolName.IMPACT_GRAPH,
                    "copy_paste": (
                        f'{cs.MCPToolName.IMPACT_GRAPH}(qualified_name="{qualified_name}", depth=3)'
                        if qualified_name
                        else (
                            f'{cs.MCPToolName.IMPACT_GRAPH}(file_path="{file_path}", depth=3)'
                            if file_path
                            else f'{cs.MCPToolName.PLAN_TASK}(goal="{focus_text or "plan target change"}")'
                        )
                    ),
                    "why": "change planning should align bundle evidence with blast radius",
                    "when": "before edits or test generation",
                }
            ]
        if bundle_name == "risk_bundle":
            return [
                {
                    "tool": cs.MCPToolName.GET_ANALYSIS_REPORT,
                    "copy_paste": f"{cs.MCPToolName.GET_ANALYSIS_REPORT}()",
                    "why": "latest normalized risk overview should be compared with full analysis summary",
                    "when": "after reviewing risk bundle",
                }
            ]
        if bundle_name == "test_bundle":
            return [
                {
                    "tool": cs.MCPToolName.TEST_GENERATE,
                    "copy_paste": f'{cs.MCPToolName.TEST_GENERATE}(goal="{focus_text or "generate impacted tests"}", output_mode="code")',
                    "why": "bundle already contains impact and coverage context for test generation",
                    "when": "after selecting impacted area",
                }
            ]
        return [
            {
                "tool": cs.MCPToolName.QUERY_CODE_GRAPH,
                "copy_paste": f'{cs.MCPToolName.QUERY_CODE_GRAPH}(natural_language_query="{focus_text or "summarize the most relevant modules"}")',
                "why": "follow normalized analysis bundle with fresh graph evidence",
                "when": "after bundle review",
            }
        ]

    def _bundle_uri(self, bundle_name: str, arguments: dict[str, str]) -> str:
        if not arguments:
            return f"{self._BUNDLE_URI_PREFIX}{bundle_name}"
        query = "&".join(
            f"{key}={value.replace(' ', '%20')}" for key, value in arguments.items()
        )
        return f"{self._BUNDLE_URI_PREFIX}{bundle_name}?{query}"

    @staticmethod
    def _prompt_description(prompt_name: str) -> str:
        descriptions = {
            "analysis_overview": "Use normalized analysis artifacts instead of raw file retrieval.",
            "architecture_review": "Review service/data/infra topology, endpoints, and dependency evidence.",
            "change_review": "Review change impact, blast radius, and affected areas before edits.",
            "risk_review": "Review security, dependency, and performance risk evidence.",
            "test_review": "Review test coverage and impacted-test evidence before generating tests.",
        }
        return descriptions.get(prompt_name, "Normalized analysis evidence prompt.")

    @staticmethod
    def _coerce_int(value: object) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return 0
