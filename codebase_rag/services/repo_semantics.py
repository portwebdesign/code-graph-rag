from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import cast

from codebase_rag.parsers.config.config_parser import ConfigParserMixin
from codebase_rag.parsers.frameworks.framework_registry import FrameworkDetectorRegistry


class RepoSemanticEnricher:
    _SKIP_DIRS = {
        ".git",
        ".idea",
        ".next",
        ".pytest_cache",
        ".ruff_cache",
        ".vscode",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "site-packages",
        "vendor",
        "venv",
    }
    _TARGET_FILE_NAMES = {
        ".env",
        "compose.yml",
        "compose.yaml",
        "composer.json",
        "docker-compose.yml",
        "docker-compose.yaml",
        "dockerfile",
        "gemfile",
        "go.mod",
        "package.json",
        "pom.xml",
        "pyproject.toml",
        "requirements.txt",
        "schema.graphql",
    }
    _TARGET_SUFFIXES = {
        ".env",
        ".graphql",
        ".java",
        ".js",
        ".json",
        ".jsx",
        ".kt",
        ".md",
        ".py",
        ".toml",
        ".ts",
        ".tsx",
        ".xml",
        ".yaml",
        ".yml",
    }
    _SERVICE_MARKERS = {
        "frontend": {"frontend", "web", "ui", "client", "app"},
        "backend": {"backend", "api", "server", "service", "services", "src"},
        "worker": {"worker", "workers", "jobs", "queue", "queues", "tasks"},
    }
    _DATASTORE_HINTS = {
        "memgraph": {"memgraph"},
        "neo4j": {"neo4j"},
        "postgres": {"postgres", "postgresql", "psycopg", "asyncpg"},
        "mysql": {"mysql", "mariadb"},
        "mongodb": {"mongodb", "mongo", "mongoose"},
        "sqlite": {"sqlite"},
    }
    _CACHE_HINTS = {
        "redis": {"redis"},
        "memcached": {"memcached"},
    }
    _QUEUE_HINTS = {
        "bullmq": {"bullmq", "bull"},
        "celery": {"celery"},
        "kafka": {"kafka"},
        "nats": {"nats"},
        "rabbitmq": {"rabbitmq"},
        "sqs": {"sqs"},
    }
    _API_STYLE_HINTS = {
        "graphql": {"graphql", "apollo", "ariadne", "strawberry"},
        "rest": {"fastapi", "flask", "express", "router.", "mapget", "rest"},
        "grpc": {"grpc", "protobuf", "proto3"},
        "websocket": {"websocket", "socket.io", "ws://"},
    }
    _INFRA_HINTS = {
        "docker": {"docker", "compose", "dockerfile"},
        "kubernetes": {
            "kubernetes",
            "kind:",
            "deployment",
            "serviceaccount",
            "ingress",
        },
        "ci": {"github/workflows", "gitlab-ci", "azure-pipelines", "circleci"},
    }
    _RUNTIME_DIRS = (
        "output/runtime",
        "output/dynamic",
        "output/profiler",
        "coverage",
        "logs",
    )

    def __init__(self) -> None:
        self._config_parser = ConfigParserMixin()

    def summarize(
        self, repo_path: str | Path, *, max_files: int = 180
    ) -> dict[str, object]:
        repo_root = Path(repo_path).resolve()
        framework_result = FrameworkDetectorRegistry(repo_root).detect_repo()
        metadata = (
            framework_result.metadata
            if isinstance(framework_result.metadata, dict)
            else {}
        )
        frameworks = self._normalize_frameworks(metadata)

        config_files: list[dict[str, object]] = []
        env_files: list[str] = []
        dockerfiles: list[str] = []
        ci_files: list[str] = []
        docker_services: set[str] = set()
        k8s_resources: set[str] = set()
        package_manifests: list[str] = []
        services: dict[str, set[str]] = {
            "frontend": set(),
            "backend": set(),
            "worker": set(),
        }
        datastores: set[str] = set()
        caches: set[str] = set()
        queues: set[str] = set()
        api_styles: set[str] = set()
        infra_styles: set[str] = set()
        topology_signals: set[str] = set()

        for path in self._iter_candidate_files(repo_root, max_files=max_files):
            relative = path.relative_to(repo_root).as_posix()
            lowered_name = path.name.lower()
            suffix = path.suffix.lower()
            text = self._safe_read_text(path)

            if lowered_name.startswith(".env"):
                env_files.append(relative)

            if lowered_name == "dockerfile" or suffix == ".dockerfile":
                dockerfiles.append(relative)
                infra_styles.add("docker")

            service_role = self._detect_service_role(relative)
            if service_role:
                services[service_role].add(relative.split("/")[0])

            config_type = self._config_parser.detect_config_type(relative)
            if config_type:
                config_files.append({"path": relative, "config_type": config_type})
                self._merge_config_insights(
                    config_type=config_type,
                    text=text,
                    file_path=relative,
                    docker_services=docker_services,
                    k8s_resources=k8s_resources,
                    ci_files=ci_files,
                    package_manifests=package_manifests,
                    datastores=datastores,
                    caches=caches,
                    queues=queues,
                    api_styles=api_styles,
                    infra_styles=infra_styles,
                    topology_signals=topology_signals,
                )

            self._merge_text_hints(
                text=text,
                relative=relative,
                datastores=datastores,
                caches=caches,
                queues=queues,
                api_styles=api_styles,
                infra_styles=infra_styles,
                topology_signals=topology_signals,
            )

            if lowered_name in {
                "package.json",
                "pyproject.toml",
                "requirements.txt",
                "go.mod",
                "pom.xml",
                "composer.json",
                "gemfile",
            }:
                package_manifests.append(relative)

        runtime_signals = self._runtime_signals(repo_root)
        summary_lines = self._build_summary_lines(
            frameworks=frameworks,
            docker_services=sorted(docker_services),
            k8s_resources=sorted(k8s_resources),
            api_styles=sorted(api_styles),
            datastores=sorted(datastores),
            caches=sorted(caches),
            queues=sorted(queues),
            runtime_signals=runtime_signals,
        )

        return {
            "summary": " | ".join(summary_lines)
            if summary_lines
            else "No framework/infra semantics detected.",
            "frameworks": frameworks,
            "framework_metadata": metadata,
            "infra": {
                "config_files": config_files,
                "docker_services": sorted(docker_services),
                "kubernetes_resources": sorted(
                    item for item in k8s_resources if not item.endswith(":")
                ),
                "ci_files": ci_files,
                "env_files": env_files,
                "dockerfiles": dockerfiles,
                "package_manifests": sorted(set(package_manifests)),
                "infra_styles": sorted(infra_styles),
            },
            "services": {
                role: sorted(
                    {
                        entry
                        for entry in roots
                        if entry and entry not in {"codebase_rag", "."}
                    }
                )
                for role, roots in services.items()
            },
            "data_systems": {
                "datastores": sorted(datastores),
                "caches": sorted(caches),
                "queues": sorted(queues),
            },
            "api_styles": sorted(api_styles),
            "topology_signals": sorted(topology_signals),
            "runtime_signals": runtime_signals,
        }

    def _iter_candidate_files(self, repo_root: Path, *, max_files: int) -> list[Path]:
        candidates: list[Path] = []
        for path in repo_root.rglob("*"):
            if len(candidates) >= max_files:
                break
            if not path.is_file():
                continue
            if self._should_skip(path, repo_root):
                continue
            lowered_name = path.name.lower()
            if lowered_name.startswith(".env"):
                candidates.append(path)
                continue
            if (
                lowered_name in self._TARGET_FILE_NAMES
                or path.suffix.lower() in self._TARGET_SUFFIXES
            ):
                candidates.append(path)
        return candidates

    def _should_skip(self, path: Path, repo_root: Path) -> bool:
        try:
            relative_parts = path.relative_to(repo_root).parts
        except ValueError:
            return True
        lowered_parts = {part.lower() for part in relative_parts}
        return not self._SKIP_DIRS.isdisjoint(lowered_parts)

    @staticmethod
    def _safe_read_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""

    @staticmethod
    def _normalize_frameworks(metadata: dict[str, object]) -> list[str]:
        raw_frameworks = metadata.get("frameworks", [])
        if not isinstance(raw_frameworks, list):
            return []
        return [str(item) for item in raw_frameworks if str(item).strip()]

    def _detect_service_role(self, relative: str) -> str | None:
        parts = {part.lower() for part in relative.split("/")}
        for role, markers in self._SERVICE_MARKERS.items():
            if not markers.isdisjoint(parts):
                return role
        return None

    def _merge_config_insights(
        self,
        *,
        config_type: str,
        text: str,
        file_path: str,
        docker_services: set[str],
        k8s_resources: set[str],
        ci_files: list[str],
        package_manifests: list[str],
        datastores: set[str],
        caches: set[str],
        queues: set[str],
        api_styles: set[str],
        infra_styles: set[str],
        topology_signals: set[str],
    ) -> None:
        if config_type == "ci-config":
            ci_files.append(file_path)
            infra_styles.add("ci")
        elif config_type == "docker-compose":
            infra_styles.add("docker")
            docker_services.update(self._extract_named_services(text))
            topology_signals.add("docker-compose_topology")
        elif config_type == "kubernetes":
            infra_styles.add("kubernetes")
            k8s_resources.update(self._extract_k8s_resources(text))
            topology_signals.add("kubernetes_topology")
        elif config_type == "package.json":
            package_manifests.append(file_path)

        self._merge_text_hints(
            text=text,
            relative=file_path,
            datastores=datastores,
            caches=caches,
            queues=queues,
            api_styles=api_styles,
            infra_styles=infra_styles,
            topology_signals=topology_signals,
        )

    def _merge_text_hints(
        self,
        *,
        text: str,
        relative: str,
        datastores: set[str],
        caches: set[str],
        queues: set[str],
        api_styles: set[str],
        infra_styles: set[str],
        topology_signals: set[str],
    ) -> None:
        lowered = f"{relative.lower()}\n{text.lower()}"

        for engine, hints in self._DATASTORE_HINTS.items():
            if any(hint in lowered for hint in hints):
                datastores.add(engine)
        for engine, hints in self._CACHE_HINTS.items():
            if any(hint in lowered for hint in hints):
                caches.add(engine)
        for engine, hints in self._QUEUE_HINTS.items():
            if any(hint in lowered for hint in hints):
                queues.add(engine)
        for api_style, hints in self._API_STYLE_HINTS.items():
            if any(hint in lowered for hint in hints):
                api_styles.add(api_style)
        for infra_style, hints in self._INFRA_HINTS.items():
            if any(hint in lowered for hint in hints):
                infra_styles.add(infra_style)

        if any(
            token in lowered
            for token in (
                "route(",
                "router.",
                "app.get(",
                "app.post(",
                "mapget(",
                "endpoint",
            )
        ):
            topology_signals.add("rest_endpoints_present")
        if any(
            token in lowered
            for token in ("graphql", ".graphql", "resolver", "query ", "mutation ")
        ):
            topology_signals.add("graphql_surface_present")
        if any(token in lowered for token in ("redis", "cache", "ttl")):
            topology_signals.add("cache_layer_present")
        if any(
            token in lowered
            for token in ("sql", "select ", "insert ", "update ", "delete ")
        ):
            topology_signals.add("database_access_present")
        if any(
            token in lowered
            for token in ("docker-compose", "service:", "deployment", "ingress")
        ):
            topology_signals.add("infra_topology_present")

        if relative.endswith("package.json"):
            self._merge_package_json_hints(
                text,
                datastores=datastores,
                caches=caches,
                queues=queues,
                api_styles=api_styles,
            )
        elif relative.endswith("pyproject.toml"):
            self._merge_pyproject_hints(
                text,
                datastores=datastores,
                caches=caches,
                queues=queues,
                api_styles=api_styles,
            )

    def _merge_package_json_hints(
        self,
        text: str,
        *,
        datastores: set[str],
        caches: set[str],
        queues: set[str],
        api_styles: set[str],
    ) -> None:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return
        dependencies: list[str] = []
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            value = payload.get(key, {})
            if isinstance(value, dict):
                dependencies.extend(str(item) for item in value.keys())
        lowered = " ".join(dependencies).lower()
        self._merge_text_hints(
            text=lowered,
            relative="package.json",
            datastores=datastores,
            caches=caches,
            queues=queues,
            api_styles=api_styles,
            infra_styles=set(),
            topology_signals=set(),
        )

    def _merge_pyproject_hints(
        self,
        text: str,
        *,
        datastores: set[str],
        caches: set[str],
        queues: set[str],
        api_styles: set[str],
    ) -> None:
        try:
            payload = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return
        project_block = payload.get("project", {})
        dependency_values: list[str] = []
        if isinstance(project_block, dict):
            raw_dependencies = project_block.get("dependencies", [])
            if isinstance(raw_dependencies, list):
                dependency_values.extend(str(item) for item in raw_dependencies)
        optional_deps = payload.get("project", {}).get("optional-dependencies", {})
        if isinstance(optional_deps, dict):
            for items in optional_deps.values():
                if isinstance(items, list):
                    dependency_values.extend(str(item) for item in items)
        lowered = " ".join(dependency_values).lower()
        self._merge_text_hints(
            text=lowered,
            relative="pyproject.toml",
            datastores=datastores,
            caches=caches,
            queues=queues,
            api_styles=api_styles,
            infra_styles=set(),
            topology_signals=set(),
        )

    @staticmethod
    def _extract_named_services(text: str) -> set[str]:
        matches = re.findall(r"^\s{2}([A-Za-z0-9_.-]+):\s*$", text, flags=re.MULTILINE)
        return {match for match in matches if match.lower() != "services"}

    @staticmethod
    def _extract_k8s_resources(text: str) -> set[str]:
        kinds = re.findall(r"^\s*kind:\s*([A-Za-z0-9]+)", text, flags=re.MULTILINE)
        names = re.findall(r"^\s*name:\s*([A-Za-z0-9_.-]+)", text, flags=re.MULTILINE)
        resources: set[str] = set()
        for kind, name in zip(kinds, names, strict=False):
            resources.add(f"{kind}:{name}")
        return resources

    def _runtime_signals(self, repo_root: Path) -> dict[str, object]:
        available_dirs: list[str] = []
        artifact_count = 0
        for relative_dir in self._RUNTIME_DIRS:
            current_dir = repo_root / relative_dir
            if not current_dir.exists():
                continue
            available_dirs.append(relative_dir)
            artifact_count += sum(
                1 for path in current_dir.rglob("*") if path.is_file()
            )
        return {
            "available_dirs": available_dirs,
            "artifact_count": artifact_count,
            "dynamic_analysis_present": bool(available_dirs),
        }

    @staticmethod
    def _build_summary_lines(
        *,
        frameworks: list[str],
        docker_services: list[str],
        k8s_resources: list[str],
        api_styles: list[str],
        datastores: list[str],
        caches: list[str],
        queues: list[str],
        runtime_signals: dict[str, object],
    ) -> list[str]:
        summary_lines: list[str] = []
        if frameworks:
            summary_lines.append("Frameworks: " + ", ".join(frameworks[:6]))
        if api_styles:
            summary_lines.append("API styles: " + ", ".join(api_styles[:4]))
        if datastores:
            summary_lines.append("Data stores: " + ", ".join(datastores[:4]))
        if caches:
            summary_lines.append("Caches: " + ", ".join(caches[:3]))
        if queues:
            summary_lines.append("Queues: " + ", ".join(queues[:3]))
        if docker_services:
            summary_lines.append("Docker services: " + ", ".join(docker_services[:6]))
        if k8s_resources:
            summary_lines.append("Kubernetes: " + ", ".join(k8s_resources[:6]))
        runtime_signals_dict = (
            runtime_signals if isinstance(runtime_signals, dict) else {}
        )
        if bool(runtime_signals_dict.get("dynamic_analysis_present", False)):
            available_dirs = runtime_signals_dict.get("available_dirs", [])
            summary_lines.append(
                "Dynamic artifacts: "
                + ", ".join(
                    str(item) for item in cast(list[object], available_dirs)[:4]
                )
            )
        return summary_lines
