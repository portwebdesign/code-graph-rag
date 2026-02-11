from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    tomllib: Any = None

from codebase_rag.core import constants as cs

from .base_module import AnalysisContext, AnalysisModule


class DependencyHealthModule(AnalysisModule):
    def get_name(self) -> str:
        return "dependency_health"

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        repo_path = context.runner.repo_path
        manifests = self._collect_manifests(repo_path)
        dependencies: list[dict[str, str]] = []

        for manifest in manifests:
            if manifest.name == cs.DEP_FILE_REQUIREMENTS:
                dependencies.extend(self._parse_requirements(manifest))
            elif manifest.name == cs.DEP_FILE_PYPROJECT:
                dependencies.extend(self._parse_pyproject(manifest))
            elif manifest.name == cs.DEP_FILE_PACKAGE_JSON:
                dependencies.extend(self._parse_package_json(manifest))

        total = len(dependencies)
        unpinned = len([dep for dep in dependencies if not self._is_pinned(dep)])
        sources: dict[str, int] = {}
        for dep in dependencies:
            source = dep.get("source", "unknown")
            sources[source] = sources.get(source, 0) + 1

        conflicts: list[dict[str, object]] = []
        versions_by_name: dict[str, set[str]] = {}
        for dep in dependencies:
            name = dep.get("name") or ""
            version = dep.get("version") or ""
            if not name:
                continue
            versions = versions_by_name.setdefault(name, set())
            if version:
                versions.add(version)

        for name, versions in versions_by_name.items():
            if len(versions) > 1:
                conflicts.append({"name": name, "versions": sorted(versions)})

        unpinned_names = sorted(
            {
                str(dep.get("name"))
                for dep in dependencies
                if dep.get("name") and not self._is_pinned(dep)
            }
        )

        lockfiles = self._detect_lockfiles(repo_path)
        warnings = self._build_warnings(total, unpinned, lockfiles)

        report = {
            "dependencies": dependencies,
            "lockfiles": lockfiles,
            "warnings": warnings,
            "conflicts": conflicts,
            "unpinned_names": unpinned_names,
        }
        context.runner._write_json_report("dependency_health_report.json", report)

        return {
            "total": total,
            "unpinned": unpinned,
            "sources": sources,
            "lockfiles": lockfiles,
            "warnings": warnings,
            "conflicts": conflicts[:50],
            "unpinned_names": unpinned_names[:50],
        }

    @staticmethod
    def _collect_manifests(repo_path: Path) -> list[Path]:
        candidates = [
            repo_path / cs.DEP_FILE_REQUIREMENTS,
            repo_path / cs.DEP_FILE_PYPROJECT,
            repo_path / cs.DEP_FILE_PACKAGE_JSON,
        ]
        return [path for path in candidates if path.exists()]

    @staticmethod
    def _parse_requirements(path: Path) -> list[dict[str, str]]:
        deps: list[dict[str, str]] = []
        content = path.read_text(encoding=cs.ENCODING_UTF8, errors="ignore")
        for raw in content.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("-"):
                continue
            name, version = DependencyHealthModule._split_requirement(line)
            if not name:
                continue
            deps.append({"name": name, "version": version, "source": path.name})
        return deps

    @staticmethod
    def _split_requirement(line: str) -> tuple[str, str]:
        for token in ("==", ">=", "<=", "~=", ">", "<"):
            if token in line:
                parts = line.split(token, 1)
                name = parts[0].strip()
                version = parts[1].strip()
                return name, f"{token}{version}"
        if "@" in line:
            name, version = [part.strip() for part in line.split("@", 1)]
            return name, version
        return line.strip(), ""

    @staticmethod
    def _parse_pyproject(path: Path) -> list[dict[str, str]]:
        if tomllib is None:
            return []
        try:
            data = tomllib.loads(path.read_text(encoding=cs.ENCODING_UTF8))
        except Exception:
            return []

        deps: list[dict[str, str]] = []
        project = data.get("project", {}) if isinstance(data, dict) else {}
        for item in project.get("dependencies", []) or []:
            name, version = DependencyHealthModule._split_requirement(str(item))
            if name:
                deps.append({"name": name, "version": version, "source": path.name})
        optional = project.get("optional-dependencies", {}) or {}
        if isinstance(optional, dict):
            for group in optional.values():
                for item in group or []:
                    name, version = DependencyHealthModule._split_requirement(str(item))
                    if name:
                        deps.append(
                            {"name": name, "version": version, "source": path.name}
                        )

        poetry = (
            data.get("tool", {}).get("poetry", {}) if isinstance(data, dict) else {}
        )
        for section in ("dependencies", "dev-dependencies"):
            items = poetry.get(section, {}) if isinstance(poetry, dict) else {}
            if isinstance(items, dict):
                for name, version in items.items():
                    if name.lower() == "python":
                        continue
                    deps.append(
                        {
                            "name": name,
                            "version": DependencyHealthModule._normalize_version(
                                version
                            ),
                            "source": path.name,
                        }
                    )
        return deps

    @staticmethod
    def _parse_package_json(path: Path) -> list[dict[str, str]]:
        try:
            data = json.loads(path.read_text(encoding=cs.ENCODING_UTF8))
        except Exception:
            return []
        deps: list[dict[str, str]] = []
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            items = data.get(section, {}) if isinstance(data, dict) else {}
            if isinstance(items, dict):
                for name, version in items.items():
                    deps.append(
                        {
                            "name": name,
                            "version": DependencyHealthModule._normalize_version(
                                version
                            ),
                            "source": path.name,
                        }
                    )
        return deps

    @staticmethod
    def _normalize_version(version: object) -> str:
        if version is None:
            return ""
        return str(version).strip()

    @staticmethod
    def _is_pinned(dep: dict[str, str]) -> bool:
        version = (dep.get("version") or "").strip()
        if not version:
            return False
        if any(token in version for token in ("*", ">", "<", "^", "~")):
            return False
        if version.startswith("=="):
            return True
        if version.startswith("="):
            return True
        return bool(version.replace(".", "").isdigit())

    @staticmethod
    def _detect_lockfiles(repo_path: Path) -> dict[str, bool]:
        lockfiles = {
            "poetry.lock": (repo_path / "poetry.lock").exists(),
            "uv.lock": (repo_path / "uv.lock").exists(),
            "package-lock.json": (repo_path / "package-lock.json").exists(),
            "yarn.lock": (repo_path / "yarn.lock").exists(),
            "pnpm-lock.yaml": (repo_path / "pnpm-lock.yaml").exists(),
        }
        return lockfiles

    @staticmethod
    def _build_warnings(
        total: int, unpinned: int, lockfiles: dict[str, bool]
    ) -> list[str]:
        warnings: list[str] = []
        if total and unpinned:
            warnings.append(f"{unpinned} dependencies are not pinned.")
        if total and not any(lockfiles.values()):
            warnings.append("No lockfile detected for dependency reproducibility.")
        if not total:
            warnings.append("No dependencies found in supported manifests.")
        return warnings
