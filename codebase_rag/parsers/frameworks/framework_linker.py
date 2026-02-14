from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from codebase_rag.core import constants as cs
from codebase_rag.data_models.types_defs import (
    FunctionRegistryTrieProtocol,
    NodeType,
    SimpleNameLookup,
)
from codebase_rag.services import IngestorProtocol


@dataclass
class EndpointMatch:
    """
    Represents a detected API endpoint or route.

    Args:
        framework (str): The framework name (e.g., 'fastapi', 'flask', 'spring').
        method (str): HTTP method (GET, POST, etc.) or 'ALL'.
        path (str): The URL path definition.
        handler_name (str | None): Name of the function/method handling the request.
        controller_name (str | None): Name of the controller class (if applicable).
        metadata (dict[str, str] | None): Additional framework-specific metadata.
    """

    framework: str
    method: str
    path: str
    handler_name: str | None = None
    controller_name: str | None = None
    metadata: dict[str, str] | None = None


class FrameworkLinker:
    """
    Orchestrates the linking of framework-specific components in the code graph.

    This class scans source files for patterns indicating framework usage (routes,
    views, DI) and creates corresponding relationships in the graph (e.g.,
    HAS_ENDPOINT, RENDERS_VIEW, PROVIDES_SERVICE).

    Attributes:
        repo_path (Path): Path to the repository root.
        project_name (str): Name of the project.
        ingestor (IngestorProtocol): Ingestor for creating nodes and relationships.
        function_registry (FunctionRegistryTrieProtocol): Registry for resolving names.
        simple_name_lookup (SimpleNameLookup): Lookup for finding qualified names.
        _template_index (dict[str, str] | None): Lazy-loaded index of template files.
        _asset_index (dict[str, str] | None): Lazy-loaded index of asset files.
    """

    def __init__(
        self,
        repo_path: Path,
        project_name: str,
        ingestor: IngestorProtocol,
        function_registry: FunctionRegistryTrieProtocol,
        simple_name_lookup: SimpleNameLookup,
    ) -> None:
        """
        Initialize the FrameworkLinker.

        Args:
            repo_path (Path): Path to the repository root.
            project_name (str): Name of the project.
            ingestor (IngestorProtocol): Ingestor for creating nodes and relationships.
            function_registry (FunctionRegistryTrieProtocol): Registry for resolving names.
            simple_name_lookup (SimpleNameLookup): Lookup for finding qualified names.
        """
        self.repo_path = repo_path
        self.project_name = project_name
        self.ingestor = ingestor
        self.function_registry = function_registry
        self.simple_name_lookup = simple_name_lookup
        self._template_index: dict[str, str] | None = None
        self._asset_index: dict[str, str] | None = None
        self._env_values = self._load_env_values()

    def link_repo(self) -> None:
        """
        Main method to link the entire repository.

        Iterates through all files in the repository and applies framework-specific
        linking logic based on file extension and content.
        """
        self._link_tailwind_assets()
        for file_path in self.repo_path.rglob("*"):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in {
                cs.EXT_CS,
                cs.EXT_PY,
                cs.EXT_JAVA,
                cs.EXT_GO,
                cs.EXT_PHP,
                cs.EXT_HTML,
                cs.EXT_HTM,
                cs.EXT_JS,
                cs.EXT_JSX,
                cs.EXT_TS,
                cs.EXT_TSX,
            }:
                continue

            try:
                source = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            if file_path.suffix.lower() == cs.EXT_CS:
                endpoints = self._extract_csharp_endpoints(source)
                self._link_endpoints(file_path, endpoints)
                service_map = self._link_csharp_di(file_path, source)
                self._link_csharp_di_consumers(file_path, source, service_map)
            elif file_path.suffix.lower() == cs.EXT_PY:
                self._link_django_views(file_path, source)
            elif file_path.suffix.lower() == cs.EXT_GO:
                endpoints = self._extract_go_endpoints(source)
                self._link_endpoints(file_path, endpoints)
                self._link_go_middleware(file_path, source)
            elif file_path.suffix.lower() == cs.EXT_PHP:
                endpoints = self._extract_php_endpoints(source)
                self._link_endpoints(file_path, endpoints)
                views = self._extract_php_views(source)
                self._link_php_views(file_path, views)
                self._link_php_eloquent_relations(file_path, source)
                self._link_wordpress_features(file_path, source)
            elif file_path.suffix.lower() == cs.EXT_JAVA:
                self._link_spring_di(file_path, source)
            elif file_path.suffix.lower() in {
                cs.EXT_HTML,
                cs.EXT_HTM,
                cs.EXT_JS,
                cs.EXT_JSX,
                cs.EXT_TS,
                cs.EXT_TSX,
            }:
                if file_path.suffix.lower() in {cs.EXT_HTML, cs.EXT_HTM}:
                    self._link_django_template_file(file_path, source)
                    self._link_template_js_chain(file_path, source)
                else:
                    self._link_nest_di(file_path, source)

                htmx_endpoints = self._extract_htmx_endpoints(source)
                self._link_htmx_endpoints(file_path, htmx_endpoints)

                next_endpoints = self._extract_next_api_endpoints(file_path, source)
                self._link_next_endpoints(file_path, next_endpoints)

    def _endpoint_qn(self, framework: str, method: str, path: str) -> str:
        """Generates a unique qualified name for an endpoint."""
        return (
            f"{self.project_name}{cs.SEPARATOR_DOT}endpoint.{framework}.{method}:{path}"
        )

    def _module_qn_for_path(self, file_path: Path) -> str:
        """Determines the module qualified name for a given file path."""
        relative_path = file_path.relative_to(self.repo_path)
        parts = list(relative_path.with_suffix("").parts)
        if file_path.name in (cs.INIT_PY, cs.MOD_RS):
            parts = list(relative_path.parent.parts)
        return cs.SEPARATOR_DOT.join([self.project_name, *parts])

    @staticmethod
    def _join_paths(prefix: str, path: str) -> str:
        """Joins a prefix and a path efficiently, handling slashes."""
        if not prefix:
            return path
        if not path:
            return prefix
        if prefix.endswith("/") and path.startswith("/"):
            return f"{prefix[:-1]}{path}"
        if not prefix.endswith("/") and not path.startswith("/"):
            return f"{prefix}/{path}"
        return f"{prefix}{path}"

    def _load_env_values(self) -> dict[str, str]:
        candidates = (
            ".env",
            ".env.local",
            ".env.development",
            ".env.production",
            ".env.test",
        )
        values: dict[str, str] = {}
        for name in candidates:
            path = self.repo_path / name
            if not path.exists():
                continue
            values.update(self._parse_env_file(path))
        return values

    @staticmethod
    def _parse_env_file(path: Path) -> dict[str, str]:
        values: dict[str, str] = {}
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return values
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("export "):
                stripped = stripped[len("export ") :]
            if "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'\"")
            if key:
                values[key] = value
        return values

    def _resolve_env_value(self, raw: str) -> str:
        if not raw:
            return raw
        pattern = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key in self._env_values:
                return self._env_values[key]
            return os.environ.get(key, "")

        return pattern.sub(replace, raw)

    def _resolve_env_expression(self, raw: str) -> str:
        if not raw:
            return raw
        resolved = raw
        env_pattern = re.compile(
            r"(?:process\.env|import\.meta\.env)\.([A-Za-z_][A-Za-z0-9_]*)"
        )

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key in self._env_values:
                return self._env_values[key]
            return os.environ.get(key, "")

        resolved = env_pattern.sub(replace, resolved)
        return self._resolve_env_value(resolved)

    @staticmethod
    def _normalize_template_literal(raw: str) -> str:
        if not raw:
            return raw
        return re.sub(r"\$\{[^}]+\}", "{param}", raw)

    @staticmethod
    def _join_url(base_url: str, path: str) -> str:
        if not base_url:
            return path
        if not path:
            return base_url
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", path) or path.startswith("//"):
            return path
        if base_url.endswith("/") and path.startswith("/"):
            return f"{base_url[:-1]}{path}"
        if not base_url.endswith("/") and not path.startswith("/"):
            return f"{base_url}/{path}"
        return f"{base_url}{path}"

    def _resolve_request_path(self, raw_path: str, base_url: str | None) -> str:
        resolved = self._resolve_env_expression(raw_path)
        resolved = self._normalize_template_literal(resolved)
        return self._join_url(base_url or "", resolved)

    def _normalize_endpoint_path(
        self, raw_path: str
    ) -> tuple[str, str | None, str | None]:
        if not raw_path:
            return "", None, None
        resolved = self._resolve_env_expression(raw_path)
        resolved = self._normalize_template_literal(resolved)
        base_url = None
        path = resolved
        match = re.match(r"^(https?://[^/]+)(/.*)?$", resolved)
        if match:
            base_url = match.group(1)
            path = match.group(2) or "/"
        path = path.replace("\\", "/")
        path = re.sub(r"\{[^/]+\}", "{param}", path)
        path = re.sub(r"\[[^/]+\]", "{param}", path)
        path = re.sub(r":[A-Za-z_][A-Za-z0-9_]*", "{param}", path)
        path = re.sub(r"\$\{[^}]+\}", "{param}", path)
        path = re.sub(r"//+", "/", path)
        if path and not path.startswith("/"):
            path = f"/{path}"
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]
        return path, base_url, raw_path

    def _extract_js_constants(self, source: str) -> dict[str, str]:
        pattern = re.compile(
            r"(?:const|let|var)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>[^;]+);"
        )
        values: dict[str, str] = {}
        for match in pattern.finditer(source):
            name = match.group("name")
            if not name:
                continue
            upper_name = name.upper()
            if (
                "API" not in upper_name
                and "BASE" not in upper_name
                and "URL" not in upper_name
            ):
                continue
            raw_value = match.group("value").strip()
            value = self._resolve_env_expression(raw_value.strip("'\"`"))
            if value:
                values[name] = value
        return values

    def _extract_js_base_urls(self, source: str) -> dict[str, str]:
        aliases: dict[str, str] = {}
        constants = self._extract_js_constants(source)
        pattern = re.compile(
            r"(?:const|let|var)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*axios\.create\(\s*\{[^}]*baseURL\s*:\s*(?P<value>[^,}]+)",
            re.IGNORECASE | re.DOTALL,
        )
        for match in pattern.finditer(source):
            name = match.group("name")
            raw_value = match.group("value").strip()
            cleaned = raw_value.strip("'\"`")
            value = constants.get(cleaned) or self._resolve_env_expression(cleaned)
            if value:
                aliases[name] = value
        return aliases

    def _ensure_endpoint_node(self, endpoint: EndpointMatch, relative_path: str) -> str:
        """Creates an endpoint node in the graph and returns its qualified name."""
        normalized_path, base_url, raw_path = self._normalize_endpoint_path(
            endpoint.path
        )
        endpoint_path = normalized_path or endpoint.path
        endpoint_qn = self._endpoint_qn(
            endpoint.framework, endpoint.method, endpoint_path
        )
        props = {
            cs.KEY_QUALIFIED_NAME: endpoint_qn,
            cs.KEY_NAME: f"{endpoint.method} {endpoint_path}",
            cs.KEY_PATH: relative_path,
            cs.KEY_FRAMEWORK: endpoint.framework,
            cs.KEY_HTTP_METHOD: endpoint.method,
            cs.KEY_ROUTE_PATH: endpoint_path,
        }
        if normalized_path and normalized_path != endpoint.path:
            props[cs.KEY_NORMALIZED_PATH] = normalized_path
        if raw_path and raw_path != endpoint_path:
            props[cs.KEY_RAW_PATH] = raw_path
        if base_url:
            props[cs.KEY_BASE_URL] = base_url
        if endpoint.metadata:
            props.update(endpoint.metadata)
        self.ingestor.ensure_node_batch(
            cs.NodeLabel.ENDPOINT,
            props,
        )
        return endpoint_qn

    def _ensure_hook_node(self, hook_name: str, hook_kind: str) -> str:
        """Creates a hook node (e.g., WordPress action/filter) and returns its QN."""
        hook_qn = f"{self.project_name}{cs.SEPARATOR_DOT}hook.{hook_kind}.{hook_name}"
        self.ingestor.ensure_node_batch(
            cs.NodeLabel.HOOK,
            {
                cs.KEY_QUALIFIED_NAME: hook_qn,
                cs.KEY_NAME: hook_name,
                cs.KEY_HOOK_NAME: hook_name,
                cs.KEY_HOOK_KIND: hook_kind,
            },
        )
        return hook_qn

    def _ensure_block_node(self, block_name: str, block_type: str) -> str:
        """Creates a block node (e.g., Gutenberg block, shortcode) and returns its QN."""
        block_qn = (
            f"{self.project_name}{cs.SEPARATOR_DOT}block.{block_type}.{block_name}"
        )
        self.ingestor.ensure_node_batch(
            cs.NodeLabel.BLOCK,
            {
                cs.KEY_QUALIFIED_NAME: block_qn,
                cs.KEY_NAME: block_name,
                cs.KEY_BLOCK_NAME: block_name,
                cs.KEY_BLOCK_TYPE: block_type,
            },
        )
        return block_qn

    def _ensure_asset_node(self, handle: str, asset_type: str, path: str | None) -> str:
        """Creates an asset node (CSS/JS) and returns its qualified name."""
        asset_qn = f"{self.project_name}{cs.SEPARATOR_DOT}asset.{asset_type}.{handle}"
        props = {
            cs.KEY_QUALIFIED_NAME: asset_qn,
            cs.KEY_NAME: handle,
            cs.KEY_ASSET_HANDLE: handle,
            cs.KEY_ASSET_TYPE: asset_type,
        }
        if path:
            props[cs.KEY_ASSET_PATH] = path
        self.ingestor.ensure_node_batch(cs.NodeLabel.ASSET, props)
        return asset_qn

    def _link_endpoints(
        self, file_path: Path, endpoints: Iterable[EndpointMatch]
    ) -> None:
        """Links endpoint nodes to their handlers and controllers."""
        if not endpoints:
            return

        relative_path = str(file_path.relative_to(self.repo_path))
        for endpoint in endpoints:
            endpoint_qn = self._ensure_endpoint_node(endpoint, relative_path)
            endpoint_props = {
                cs.KEY_RELATION_TYPE: endpoint.framework,
                "http_method": endpoint.method,
                "route_path": endpoint.path,
                "auth_required": False,
                "framework": endpoint.framework,
                "source_parser": "framework_linker",
            }

            handler_qn = None
            if endpoint.handler_name:
                handler_qn = self._find_best_handler_qn(
                    endpoint.handler_name, endpoint.controller_name
                )

            if handler_qn:
                handler_type = self.function_registry.get(handler_qn)
                if handler_type:
                    self.ingestor.ensure_relationship_batch(
                        (handler_type.value, cs.KEY_QUALIFIED_NAME, handler_qn),
                        cs.RelationshipType.HAS_ENDPOINT,
                        (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn),
                        endpoint_props,
                    )

            if endpoint.controller_name:
                controller_qn = self._find_best_controller_qn(endpoint.controller_name)
                if controller_qn:
                    self.ingestor.ensure_relationship_batch(
                        (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn),
                        cs.RelationshipType.ROUTES_TO_CONTROLLER,
                        (cs.NodeLabel.CLASS, cs.KEY_QUALIFIED_NAME, controller_qn),
                        endpoint_props,
                    )
                    if handler_qn and handler_type:
                        self.ingestor.ensure_relationship_batch(
                            (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn),
                            cs.RelationshipType.ROUTES_TO_ACTION,
                            (handler_type.value, cs.KEY_QUALIFIED_NAME, handler_qn),
                            endpoint_props,
                        )

    def _find_best_handler_qn(
        self, handler_name: str, controller_name: str | None
    ) -> str | None:
        """Attempts to resolve the qualified name of a handler function/method."""
        simple_name = handler_name.split(cs.SEPARATOR_DOT)[-1]
        candidates = self.function_registry.find_ending_with(simple_name)
        if not candidates:
            return None

        if controller_name:
            controller_qn = self._find_best_controller_qn(controller_name)
            if controller_qn:
                for qn in candidates:
                    if qn.startswith(f"{controller_qn}{cs.SEPARATOR_DOT}"):
                        return qn
        return candidates[0]

    def _find_best_qn_by_simple_name(self, name: str) -> str | None:
        """Resolves a qualified name from a simple name using the registry."""
        candidates = self.function_registry.find_ending_with(name)
        return candidates[0] if candidates else None

    def _find_best_controller_qn(self, controller_name: str) -> str | None:
        """Resolves the qualified name of a controller class."""
        candidates = self.function_registry.find_ending_with(controller_name)
        for qn in candidates:
            if self.function_registry.get(qn) == NodeType.CLASS:
                return qn
        return candidates[0] if candidates else None

    def _extract_csharp_endpoints(self, source: str) -> list[EndpointMatch]:
        """Extracts ASP.NET Core endpoints from C# source code."""
        endpoints: list[EndpointMatch] = []

        class_pattern = re.compile(
            r"(?:\[\s*Route\s*\(\s*\"(?P<route>[^\"]+)\"\s*\)\s*\]\s*)?"
            r"(?:public\s+)?class\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*[A-Za-z0-9_\.]+Controller",
            re.IGNORECASE,
        )

        class_ranges: list[tuple[int, int, str, str]] = []
        matches = list(class_pattern.finditer(source))
        for idx, match in enumerate(matches):
            start = match.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(source)
            name = match.group("name")
            route = match.group("route") or ""
            route = route.replace(
                "[controller]", name.replace("Controller", "").lower()
            )
            class_ranges.append((start, end, name, route))

        method_pattern = re.compile(
            r"\[\s*Http(?P<method>Get|Post|Put|Delete|Patch|Options|Head)\s*(?:\(\s*\"(?P<path>[^\"]*)\"\s*\))?\s*\]"
            r"[\s\S]{0,200}?\b(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(",
            re.IGNORECASE,
        )

        route_only_pattern = re.compile(
            r"\[\s*Route\s*\(\s*\"(?P<path>[^\"]+)\"\s*\)\s*\]"
            r"[\s\S]{0,200}?\b(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(",
            re.IGNORECASE,
        )

        for start, end, controller_name, controller_prefix in class_ranges:
            class_block = source[start:end]
            for match in method_pattern.finditer(class_block):
                method = match.group("method").upper()
                path = match.group("path") or ""
                handler = match.group("name")
                full_path = self._join_paths(controller_prefix, path)
                endpoints.append(
                    EndpointMatch(
                        framework="aspnet",
                        method=method,
                        path=full_path,
                        handler_name=handler,
                        controller_name=controller_name,
                    )
                )

            for match in route_only_pattern.finditer(class_block):
                path = match.group("path") or ""
                handler = match.group("name")
                full_path = self._join_paths(controller_prefix, path)
                endpoints.append(
                    EndpointMatch(
                        framework="aspnet",
                        method="GET",
                        path=full_path,
                        handler_name=handler,
                        controller_name=controller_name,
                    )
                )

        minimal_pattern = re.compile(
            r"\.Map(?P<method>Get|Post|Put|Delete|Patch)\s*\(\s*\"(?P<path>[^\"]+)\"\s*,\s*(?P<handler>[A-Za-z_][A-Za-z0-9_\.]+)",
            re.IGNORECASE,
        )
        for match in minimal_pattern.finditer(source):
            method = match.group("method").upper()
            path = match.group("path")
            handler = match.group("handler")
            endpoints.append(
                EndpointMatch(
                    framework="aspnet",
                    method=method,
                    path=path,
                    handler_name=handler.split(cs.SEPARATOR_DOT)[-1],
                )
            )

        return endpoints

    def _extract_go_endpoints(self, source: str) -> list[EndpointMatch]:
        """Extracts Go web framework endpoints (Gin, Echo, Fiber, Chi) from source."""
        endpoints: list[EndpointMatch] = []
        group_pattern = re.compile(
            r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*:=\s*\w+\.Group\(\s*\"(?P<prefix>[^\"]+)\"",
            re.IGNORECASE,
        )
        groups: dict[str, str] = {}
        for match in group_pattern.finditer(source):
            groups[match.group("name")] = match.group("prefix")

        chained_group_pattern = re.compile(
            r"\.Group\(\s*\"(?P<prefix>[^\"]+)\"\s*\)\s*\.\s*(?P<method>GET|POST|PUT|DELETE|PATCH)\s*\(\s*\"(?P<path>[^\"]+)\"\s*,\s*(?P<handler>[A-Za-z_][A-Za-z0-9_\.]+)",
            re.IGNORECASE,
        )
        for match in chained_group_pattern.finditer(source):
            prefix = match.group("prefix")
            method = match.group("method").upper()
            path = self._join_paths(prefix, match.group("path"))
            handler = match.group("handler")
            endpoints.append(
                EndpointMatch(
                    framework="go_web",
                    method=method,
                    path=path,
                    handler_name=handler.split(cs.SEPARATOR_DOT)[-1],
                )
            )

        pattern = re.compile(
            r"(?P<prefix>[A-Za-z_][A-Za-z0-9_]*)?\.?\s*(?P<method>GET|POST|PUT|DELETE|PATCH)\s*\(\s*\"(?P<path>[^\"]+)\"\s*,\s*(?P<handler>[A-Za-z_][A-Za-z0-9_\.]+)",
            re.IGNORECASE,
        )
        for match in pattern.finditer(source):
            method = match.group("method").upper()
            path = match.group("path")
            handler = match.group("handler")
            prefix_name = match.group("prefix")
            if prefix_name and prefix_name in groups:
                path = self._join_paths(groups[prefix_name], path)
            endpoints.append(
                EndpointMatch(
                    framework="go_web",
                    method=method,
                    path=path,
                    handler_name=handler.split(cs.SEPARATOR_DOT)[-1],
                )
            )
        return endpoints

    def _extract_php_endpoints(self, source: str) -> list[EndpointMatch]:
        """Extracts PHP framework endpoints (Laravel, Symfony)."""
        endpoints: list[EndpointMatch] = []
        pattern_array = re.compile(
            r"Route::(?P<method>get|post|put|patch|delete|options|any)\s*\(\s*['\"](?P<path>[^'\"]+)['\"]\s*,\s*\[(?P<controller>[A-Za-z_][A-Za-z0-9_]*)::class\s*,\s*['\"](?P<action>[A-Za-z_][A-Za-z0-9_]*)['\"]\]\s*\)",
            re.IGNORECASE,
        )
        for match in pattern_array.finditer(source):
            method = match.group("method").upper()
            path = match.group("path")
            controller = match.group("controller")
            action = match.group("action")
            endpoints.append(
                EndpointMatch(
                    framework="laravel",
                    method=method,
                    path=path,
                    handler_name=action,
                    controller_name=controller,
                )
            )

        pattern_at = re.compile(
            r"Route::(?P<method>get|post|put|patch|delete|options|any)\s*\(\s*['\"](?P<path>[^'\"]+)['\"]\s*,\s*['\"](?P<controller>[A-Za-z_][A-Za-z0-9_]*)@(?P<action>[A-Za-z_][A-Za-z0-9_]*)['\"]\s*\)",
            re.IGNORECASE,
        )
        for match in pattern_at.finditer(source):
            method = match.group("method").upper()
            path = match.group("path")
            controller = match.group("controller")
            action = match.group("action")
            endpoints.append(
                EndpointMatch(
                    framework="laravel",
                    method=method,
                    path=path,
                    handler_name=action,
                    controller_name=controller,
                )
            )

        pattern_symfony = re.compile(
            r"#\[Route\(\s*['\"](?P<path>[^'\"]+)['\"](?:[^\]]*methods:\s*\[(?P<methods>[^\]]*)\])?",
            re.IGNORECASE,
        )
        for match in pattern_symfony.finditer(source):
            path = match.group("path")
            methods_raw = match.group("methods") or ""
            methods = [
                m.strip().strip("'\"") for m in methods_raw.split(",") if m.strip()
            ]
            if not methods:
                methods = ["GET"]
            for method in methods:
                endpoints.append(
                    EndpointMatch(
                        framework="symfony",
                        method=method.upper(),
                        path=path,
                    )
                )

        return endpoints

    def _extract_php_views(self, source: str) -> list[str]:
        """Extracts referenced View templates in PHP code."""
        views: list[str] = []
        view_pattern = re.compile(
            r"view\(\s*['\"](?P<view>[^'\"]+)['\"]", re.IGNORECASE
        )
        for match in view_pattern.finditer(source):
            views.append(match.group("view"))
        return list(dict.fromkeys(views))

    def _link_go_middleware(self, file_path: Path, source: str) -> None:
        """Links Go middleware to modules based on usage."""
        module_qn = self._module_qn_for_path(file_path)
        middleware_names: list[str] = []

        use_pattern = re.compile(r"\.Use\s*\((?P<args>[^\)]*)\)")
        for match in use_pattern.finditer(source):
            args = match.group("args")
            for token in args.split(","):
                name = token.strip().split("(")[0].strip()
                if name:
                    middleware_names.append(name.split(cs.SEPARATOR_DOT)[-1])

        for index, name in enumerate(dict.fromkeys(middleware_names)):
            target_qn = self._find_best_qn_by_simple_name(name)
            if not target_qn:
                continue
            target_type = self.function_registry.get(target_qn)
            if not target_type:
                continue
            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
                cs.RelationshipType.USES_MIDDLEWARE,
                (target_type.value, cs.KEY_QUALIFIED_NAME, target_qn),
                {
                    cs.KEY_RELATION_TYPE: "middleware",
                    cs.KEY_LIFETIME: "request",
                    cs.KEY_MIDDLEWARE_ORDER: index,
                },
            )

    def _link_csharp_di(self, file_path: Path, source: str) -> dict[str, str]:
        """Links C# Dependency Injection registrations in Startup.cs/Program.cs."""
        module_qn = self._module_qn_for_path(file_path)
        service_map: dict[str, str] = {}
        generic_pair_pattern = re.compile(
            r"(?:Try)?Add(?P<lifetime>Scoped|Singleton|Transient)\s*<\s*(?P<iface>[^,>]+)\s*,\s*(?P<impl>[^>]+)\s*>",
            re.IGNORECASE,
        )
        generic_single_pattern = re.compile(
            r"(?:Try)?Add(?P<lifetime>Scoped|Singleton|Transient)\s*<\s*(?P<service>[^,>]+)\s*>",
            re.IGNORECASE,
        )
        typeof_pair_pattern = re.compile(
            r"(?:Try)?Add(?P<lifetime>Scoped|Singleton|Transient)\s*\(\s*typeof\((?P<iface>[^\)]+)\)\s*,\s*typeof\((?P<impl>[^\)]+)\)\s*\)",
            re.IGNORECASE,
        )

        for match in generic_pair_pattern.finditer(source):
            lifetime = match.group("lifetime").lower()
            iface_name = self._normalize_csharp_type_name(match.group("iface"))
            impl_name = self._normalize_csharp_type_name(match.group("impl"))
            self._register_csharp_service(module_qn, iface_name, lifetime, service_map)
            self._register_csharp_service(module_qn, impl_name, lifetime, service_map)

        for match in typeof_pair_pattern.finditer(source):
            lifetime = match.group("lifetime").lower()
            iface_name = self._normalize_csharp_type_name(match.group("iface"))
            impl_name = self._normalize_csharp_type_name(match.group("impl"))
            self._register_csharp_service(module_qn, iface_name, lifetime, service_map)
            self._register_csharp_service(module_qn, impl_name, lifetime, service_map)

        for match in generic_single_pattern.finditer(source):
            lifetime = match.group("lifetime").lower()
            service_name = self._normalize_csharp_type_name(match.group("service"))
            self._register_csharp_service(
                module_qn, service_name, lifetime, service_map
            )

        return service_map

    def _link_php_eloquent_relations(self, file_path: Path, source: str) -> None:
        """Links PHP Eloquent models based on relationship methods."""
        class_pattern = re.compile(
            r"class\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+extends\s+Model",
            re.IGNORECASE,
        )
        relation_pattern = re.compile(
            r"return\s+\$this->(?P<relation>hasOne|hasMany|belongsTo|belongsToMany|morphOne|morphMany|morphTo|morphedByMany|morphToMany)\s*\(\s*(?P<target>[A-Za-z_][A-Za-z0-9_\\]+)::class",
            re.IGNORECASE,
        )

        models: list[str] = []
        for match in class_pattern.finditer(source):
            models.append(match.group("name"))

        if not models:
            return

        source_model = models[0]
        source_qn = self._find_best_qn_by_simple_name(source_model)
        if not source_qn:
            return

        for match in relation_pattern.finditer(source):
            relation_type = match.group("relation")
            target_name = match.group("target").split("\\")[-1]
            target_qn = self._find_best_qn_by_simple_name(target_name)
            if not target_qn:
                continue

            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.CLASS, cs.KEY_QUALIFIED_NAME, source_qn),
                cs.RelationshipType.ELOQUENT_RELATION,
                (cs.NodeLabel.CLASS, cs.KEY_QUALIFIED_NAME, target_qn),
                {cs.KEY_RELATION_TYPE: relation_type},
            )

    def _link_wordpress_features(self, file_path: Path, source: str) -> None:
        """Links WordPress specific features like hooks, REST routes, and shortcodes."""
        if not self._is_wordpress_context(file_path, source):
            return

        module_qn = self._module_qn_for_path(file_path)

        hook_pattern = re.compile(
            r"add_(action|filter)\s*\(\s*['\"](?P<hook>[^'\"]+)['\"]\s*,\s*(?P<handler>[^\)\,]+)",
            re.IGNORECASE,
        )
        for match in hook_pattern.finditer(source):
            kind = match.group(1).lower()
            hook_name = match.group("hook")
            handler_raw = match.group("handler").strip().strip("'\"")
            handler_name = handler_raw.split("::")[-1].split(cs.SEPARATOR_DOT)[-1]
            hook_qn = self._ensure_hook_node(hook_name, kind)
            handler_qn = self._find_best_handler_qn(handler_name, None)
            if handler_qn:
                handler_type = self.function_registry.get(handler_qn)
                if handler_type:
                    self.ingestor.ensure_relationship_batch(
                        (handler_type.value, cs.KEY_QUALIFIED_NAME, handler_qn),
                        cs.RelationshipType.HOOKS,
                        (cs.NodeLabel.HOOK, cs.KEY_QUALIFIED_NAME, hook_qn),
                        {
                            cs.KEY_RELATION_TYPE: "hook",
                            cs.KEY_HOOK_KIND: kind,
                        },
                    )
            else:
                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
                    cs.RelationshipType.HOOKS,
                    (cs.NodeLabel.HOOK, cs.KEY_QUALIFIED_NAME, hook_qn),
                    {
                        cs.KEY_RELATION_TYPE: "hook",
                        cs.KEY_HOOK_KIND: kind,
                    },
                )

        rest_pattern = re.compile(
            r"register_rest_route\s*\(\s*['\"](?P<ns>[^'\"]+)['\"]\s*,\s*['\"](?P<route>[^'\"]+)['\"]\s*,\s*[^\)]*?['\"]methods['\"]\s*=>\s*['\"](?P<methods>[^'\"]+)['\"][^\)]*?['\"]callback['\"]\s*=>\s*(?P<callback>[^\)\,\]]+)",
            re.IGNORECASE,
        )
        for match in rest_pattern.finditer(source):
            namespace = match.group("ns")
            route = match.group("route")
            methods = match.group("methods").split("|")
            callback = match.group("callback").strip().strip("'\"")
            handler_name = callback.split("::")[-1].split(cs.SEPARATOR_DOT)[-1]
            for method in methods:
                endpoint = EndpointMatch(
                    framework="wordpress",
                    method=method.upper(),
                    path=f"/{namespace}{route}",
                    handler_name=handler_name,
                )
                endpoint_qn = self._ensure_endpoint_node(
                    endpoint, str(file_path.relative_to(self.repo_path))
                )
                handler_qn = self._find_best_handler_qn(handler_name, None)
                if handler_qn:
                    handler_type = self.function_registry.get(handler_qn)
                    if handler_type:
                        self.ingestor.ensure_relationship_batch(
                            (handler_type.value, cs.KEY_QUALIFIED_NAME, handler_qn),
                            cs.RelationshipType.HAS_ENDPOINT,
                            (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn),
                            {cs.KEY_RELATION_TYPE: "wordpress_rest"},
                        )

        shortcode_pattern = re.compile(
            r"add_shortcode\s*\(\s*['\"](?P<tag>[^'\"]+)['\"]\s*,\s*(?P<handler>[^\)\,]+)",
            re.IGNORECASE,
        )
        for match in shortcode_pattern.finditer(source):
            tag = match.group("tag")
            handler_raw = match.group("handler").strip().strip("'\"")
            handler_name = handler_raw.split("::")[-1].split(cs.SEPARATOR_DOT)[-1]
            block_qn = self._ensure_block_node(tag, "shortcode")
            handler_qn = self._find_best_handler_qn(handler_name, None)
            if handler_qn:
                handler_type = self.function_registry.get(handler_qn)
                if handler_type:
                    self.ingestor.ensure_relationship_batch(
                        (handler_type.value, cs.KEY_QUALIFIED_NAME, handler_qn),
                        cs.RelationshipType.REGISTERS_BLOCK,
                        (cs.NodeLabel.BLOCK, cs.KEY_QUALIFIED_NAME, block_qn),
                        {cs.KEY_RELATION_TYPE: "shortcode"},
                    )

        block_pattern = re.compile(
            r"register_block_type\s*\(\s*['\"](?P<block>[^'\"]+)['\"]\s*,\s*\[(?P<args>[^\]]*)\]\s*\)",
            re.IGNORECASE,
        )
        render_pattern = re.compile(
            r"['\"]render_callback['\"]\s*=>\s*(?P<callback>[^,\]]+)",
            re.IGNORECASE,
        )
        for match in block_pattern.finditer(source):
            block_name = match.group("block")
            args = match.group("args")
            callback_match = render_pattern.search(args)
            handler_name = None
            if callback_match:
                callback = callback_match.group("callback").strip().strip("'\"")
                handler_name = callback.split("::")[-1].split(cs.SEPARATOR_DOT)[-1]
            block_qn = self._ensure_block_node(block_name, "gutenberg")
            if handler_name:
                handler_qn = self._find_best_handler_qn(handler_name, None)
                if handler_qn:
                    handler_type = self.function_registry.get(handler_qn)
                    if handler_type:
                        self.ingestor.ensure_relationship_batch(
                            (handler_type.value, cs.KEY_QUALIFIED_NAME, handler_qn),
                            cs.RelationshipType.REGISTERS_BLOCK,
                            (cs.NodeLabel.BLOCK, cs.KEY_QUALIFIED_NAME, block_qn),
                            {cs.KEY_RELATION_TYPE: "gutenberg"},
                        )

        enqueue_pattern = re.compile(
            r"wp_enqueue_(script|style)\s*\(\s*['\"](?P<handle>[^'\"]+)['\"]\s*,\s*(?P<path>[^\)\,]+)",
            re.IGNORECASE,
        )
        for match in enqueue_pattern.finditer(source):
            asset_type = match.group(1).lower()
            handle = match.group("handle")
            raw_path = match.group("path").strip().strip("'\"")
            asset_qn = self._ensure_asset_node(handle, asset_type, raw_path)
            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
                cs.RelationshipType.USES_ASSET,
                (cs.NodeLabel.ASSET, cs.KEY_QUALIFIED_NAME, asset_qn),
                {cs.KEY_RELATION_TYPE: "enqueue"},
            )

    def _is_wordpress_context(self, file_path: Path, source: str) -> bool:
        """Checks if the file or project context indicates a WordPress environment."""
        if "Plugin Name:" in source:
            return True
        if "wp-content" in str(file_path).lower():
            return True
        if (self.repo_path / "wp-config.php").exists():
            return True
        return False

    def _extract_htmx_endpoints(self, source: str) -> list[EndpointMatch]:
        """Extracts HTMX triggered endpoints from HTML/Template attributes."""
        endpoints: list[EndpointMatch] = []
        element_pattern = re.compile(
            r"<[^>]*\bhx-(get|post|put|delete|patch)\s*=\s*['\"](?P<path>[^'\"]+)['\"][^>]*>",
            re.IGNORECASE,
        )
        for match in element_pattern.finditer(source):
            element_text = match.group(0)
            method = match.group(1).upper()
            path = match.group("path")

            metadata: dict[str, str] = {}
            trigger = self._extract_attribute_value(element_text, "hx-trigger")
            target = self._extract_attribute_value(element_text, "hx-target")
            swap = self._extract_attribute_value(element_text, "hx-swap")
            if trigger:
                metadata[cs.KEY_HTMX_TRIGGER] = trigger
            if target:
                metadata[cs.KEY_HTMX_TARGET] = target
            if swap:
                metadata[cs.KEY_HTMX_SWAP] = swap

            endpoints.append(
                EndpointMatch(
                    framework="htmx",
                    method=method,
                    path=path,
                    metadata=metadata or None,
                )
            )
        return endpoints

    def _link_htmx_endpoints(
        self, file_path: Path, endpoints: list[EndpointMatch]
    ) -> None:
        """Links HTMX requests in templates to their backend endpoints."""
        if not endpoints:
            return
        relative_path = str(file_path.relative_to(self.repo_path))
        module_qn = self._module_qn_for_path(file_path)
        for endpoint in endpoints:
            endpoint_qn = self._ensure_endpoint_node(endpoint, relative_path)
            props = {cs.KEY_RELATION_TYPE: "htmx"}
            if endpoint.metadata:
                props.update(endpoint.metadata)
            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
                cs.RelationshipType.REQUESTS_ENDPOINT,
                (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn),
                props,
            )

    @staticmethod
    def _extract_attribute_value(text: str, attr_name: str) -> str | None:
        """Helper to extract an attribute value from an HTML tag string."""
        pattern = re.compile(
            rf"{re.escape(attr_name)}\s*=\s*['\"]([^'\"]+)['\"]",
            re.IGNORECASE,
        )
        match = pattern.search(text)
        return match.group(1) if match else None

    def _extract_next_api_endpoints(
        self, file_path: Path, source: str
    ) -> list[EndpointMatch]:
        """Extracts Next.js API routes (Pages Router and App Router)."""
        path_str = str(file_path).replace("\\", "/")
        endpoints: list[EndpointMatch] = []

        if "/pages/api/" in path_str:
            route = path_str.split("/pages/api/")[1]
            route = route.rsplit(".", 1)[0]
            if route.endswith("/index"):
                route = route[: -len("/index")]
            api_path = f"/api/{route}" if route else "/api"
            endpoints.append(
                EndpointMatch(
                    framework="next",
                    method="ALL",
                    path=api_path,
                    handler_name="handler",
                )
            )
            return endpoints

        if "/app/api/" in path_str and (
            path_str.endswith("/route.ts")
            or path_str.endswith("/route.js")
            or path_str.endswith("/route.tsx")
            or path_str.endswith("/route.jsx")
        ):
            route = path_str.split("/app/api/")[1]
            route = (
                route.replace("/route.ts", "")
                .replace("/route.js", "")
                .replace("/route.tsx", "")
                .replace("/route.jsx", "")
            )
            api_path = f"/api/{route}" if route else "/api"
            method_pattern = re.compile(
                r"export\s+async\s+function\s+(GET|POST|PUT|DELETE|PATCH)|export\s+function\s+(GET|POST|PUT|DELETE|PATCH)",
                re.IGNORECASE,
            )
            methods = []
            for match in method_pattern.finditer(source):
                method = (match.group(1) or match.group(2) or "").upper()
                if method:
                    methods.append(method)
            if not methods:
                methods = ["ALL"]
            for method in methods:
                endpoints.append(
                    EndpointMatch(
                        framework="next",
                        method=method,
                        path=api_path,
                        handler_name=method if method != "ALL" else None,
                    )
                )
            return endpoints

        return endpoints

    def _link_next_endpoints(
        self, file_path: Path, endpoints: list[EndpointMatch]
    ) -> None:
        """Links Next.js API routes to their handlers."""
        if not endpoints:
            return
        relative_path = str(file_path.relative_to(self.repo_path))
        module_qn = self._module_qn_for_path(file_path)
        for endpoint in endpoints:
            endpoint_qn = self._ensure_endpoint_node(endpoint, relative_path)
            endpoint_props = {
                cs.KEY_RELATION_TYPE: "next_api",
                "http_method": endpoint.method,
                "route_path": endpoint.path,
                "auth_required": False,
                "framework": "next",
                "source_parser": "framework_linker",
            }
            handler_qn = None
            if endpoint.handler_name:
                handler_qn = self._find_best_handler_qn(endpoint.handler_name, None)
            if handler_qn:
                handler_type = self.function_registry.get(handler_qn)
                if handler_type:
                    self.ingestor.ensure_relationship_batch(
                        (handler_type.value, cs.KEY_QUALIFIED_NAME, handler_qn),
                        cs.RelationshipType.HAS_ENDPOINT,
                        (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn),
                        endpoint_props,
                    )
            else:
                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
                    cs.RelationshipType.HAS_ENDPOINT,
                    (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn),
                    endpoint_props,
                )

    def _link_tailwind_assets(self) -> None:
        """Links the project to Tailwind CSS if configured."""
        tailwind_files = list(self.repo_path.glob("tailwind.config.*"))
        package_json = self.repo_path / "package.json"
        has_tailwind = bool(tailwind_files)

        if package_json.exists():
            content = package_json.read_text(encoding="utf-8", errors="ignore")
            if "tailwindcss" in content:
                has_tailwind = True

        if not has_tailwind:
            return

        asset_qn = self._ensure_asset_node("tailwindcss", "css_framework", None)
        self.ingestor.ensure_relationship_batch(
            (cs.NodeLabel.PROJECT, cs.KEY_NAME, self.project_name),
            cs.RelationshipType.USES_ASSET,
            (cs.NodeLabel.ASSET, cs.KEY_QUALIFIED_NAME, asset_qn),
            {cs.KEY_RELATION_TYPE: "tailwind"},
        )

    def _link_php_views(self, file_path: Path, views: list[str]) -> None:
        """Links PHP controllers to the Blade views they render."""
        if not views:
            return

        controllers = self._extract_php_controllers(file_path)
        if not controllers:
            return

        for controller_name in controllers:
            controller_qn = self._find_best_controller_qn(controller_name)
            if not controller_qn:
                continue

            for view_name in views:
                view_path = self._resolve_view_path(view_name)
                if not view_path:
                    continue

                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.CLASS, cs.KEY_QUALIFIED_NAME, controller_qn),
                    cs.RelationshipType.RENDERS_VIEW,
                    (cs.NodeLabel.FILE, cs.KEY_PATH, view_path),
                    {cs.KEY_RELATION_TYPE: "blade"},
                )

    def _extract_php_controllers(self, file_path: Path) -> list[str]:
        """Extracts PHP controller classes defined in a file."""
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return []

        controllers: list[str] = []
        pattern = re.compile(
            r"class\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+extends\s+Controller",
            re.IGNORECASE,
        )
        for match in pattern.finditer(content):
            controllers.append(match.group("name"))
        return list(dict.fromkeys(controllers))

    def _resolve_view_path(self, view_name: str) -> str | None:
        """Resolves a Laravel/Blade view name (e.g., 'auth.login') to a file path."""
        normalized = view_name.replace(".", "/")
        candidates = [
            self.repo_path / "resources" / "views" / f"{normalized}.blade.php",
            self.repo_path / "resources" / "views" / normalized / "index.blade.php",
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate.relative_to(self.repo_path))
        return None

    def _get_template_index(self) -> dict[str, str]:
        """Lazy-loads an index of template files (HTML, Django, Jinja2)."""
        if self._template_index is None:
            from .django_template_parser import DjangoTemplateParser

            self._template_index = DjangoTemplateParser.build_template_index(
                self.repo_path
            )
        return self._template_index

    def _get_asset_index(self) -> dict[str, str]:
        """Lazy-loads an index of static assets (CSS, JS, TS, images)."""
        if self._asset_index is None:
            self._asset_index = {}
            for ext in (cs.EXT_JS, cs.EXT_JSX, cs.EXT_TS, cs.EXT_TSX):
                for file_path in self.repo_path.rglob(f"*{ext}"):
                    try:
                        rel = str(file_path.relative_to(self.repo_path)).replace(
                            "\\", "/"
                        )
                    except ValueError:
                        continue
                    if rel not in self._asset_index:
                        self._asset_index[rel] = rel
                    filename = file_path.name
                    if filename not in self._asset_index:
                        self._asset_index[filename] = rel
        return self._asset_index

    def _resolve_template_path(self, template_name: str) -> str | None:
        """Resolves a template name to a file path using the index."""
        if not template_name:
            return None
        normalized = template_name.strip().strip("\"'").replace("\\", "/")
        index = self._get_template_index()
        if normalized in index:
            return index[normalized]
        suffix_match = next(
            (path for key, path in index.items() if key.endswith(normalized)), None
        )
        return suffix_match

    def _resolve_asset_path(self, base_dir: Path, asset_ref: str) -> str | None:
        """Resolves an asset reference to a file path."""
        if not asset_ref:
            return None

        static_match = re.search(r"{%\s*static\s+['\"]([^'\"]+)['\"]\s*%}", asset_ref)
        if static_match:
            asset_ref = static_match.group(1)

        if asset_ref.startswith("{{") or asset_ref.startswith("{%"):
            return None

        asset_ref = asset_ref.split("?")[0].split("#")[0]
        asset_ref = asset_ref.strip().strip("\"'")

        if asset_ref.startswith("http://") or asset_ref.startswith("https://"):
            return None

        if asset_ref.startswith("/"):
            asset_ref = asset_ref.lstrip("/")

        candidate = (base_dir / asset_ref).resolve()
        try:
            if candidate.exists() and candidate.is_file():
                return str(candidate.relative_to(self.repo_path))
        except Exception:
            pass

        index = self._get_asset_index()
        if asset_ref in index:
            return index[asset_ref]
        suffix_match = next(
            (path for key, path in index.items() if key.endswith(asset_ref)), None
        )
        return suffix_match

    def _link_django_template_file(self, file_path: Path, source: str) -> None:
        """Links Django views to templates referenced in `render()` calls."""
        if "{{" not in source and "{%" not in source:
            return
        from .django_template_parser import DjangoTemplateParser

        parser = DjangoTemplateParser(self.repo_path, self.project_name, self.ingestor)
        extraction = parser.parse_template(file_path, source)
        if (
            not extraction.tags
            and not extraction.variables
            and not extraction.includes
            and not extraction.extends
        ):
            return
        parser.ingest_template(file_path, extraction, self._get_template_index())

    def _link_django_views(self, file_path: Path, source: str) -> None:
        """Links Django Class-Based Views (CBVs) to templates."""
        view_templates = self._extract_python_view_templates(source)
        if not view_templates:
            return

        module_qn = self._module_qn_for_path(file_path)
        for handler_name, controller_name, template_name in view_templates:
            template_path = self._resolve_template_path(template_name)
            if not template_path:
                continue

            handler_qn = None
            handler_type = None
            if handler_name:
                handler_qn = self._find_best_handler_qn(handler_name, controller_name)
                handler_type = (
                    self.function_registry.get(handler_qn) if handler_qn else None
                )

            if handler_qn and handler_type:
                self.ingestor.ensure_relationship_batch(
                    (handler_type.value, cs.KEY_QUALIFIED_NAME, handler_qn),
                    cs.RelationshipType.RENDERS_VIEW,
                    (cs.NodeLabel.FILE, cs.KEY_PATH, template_path),
                    {cs.KEY_RELATION_TYPE: "django"},
                )
            else:
                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
                    cs.RelationshipType.RENDERS_VIEW,
                    (cs.NodeLabel.FILE, cs.KEY_PATH, template_path),
                    {cs.KEY_RELATION_TYPE: "django"},
                )

    def _extract_python_view_templates(
        self, source: str
    ) -> list[tuple[str | None, str | None, str]]:
        """Extracts template names from Python source code (Django views)."""
        lines = source.splitlines()
        results: list[tuple[str | None, str | None, str]] = []
        class_stack: list[tuple[str, int]] = []
        current_def: tuple[str, int] | None = None
        class_template: dict[str, str] = {}

        render_pattern = re.compile(
            r"\brender(?:_to_response)?\s*\(\s*[^,]+,\s*['\"]([^'\"]+)['\"]",
            re.IGNORECASE,
        )
        template_response_pattern = re.compile(
            r"TemplateResponse\s*\(\s*[^,]+,\s*['\"]([^'\"]+)['\"]",
            re.IGNORECASE,
        )
        template_name_pattern = re.compile(r"template_name\s*=\s*['\"]([^'\"]+)['\"]")

        for line in lines:
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            if not stripped:
                continue

            while (
                class_stack
                and indent <= class_stack[-1][1]
                and not stripped.startswith("#")
            ):
                class_stack.pop()

            if stripped.startswith("class "):
                match = re.match(r"class\s+([A-Za-z_][A-Za-z0-9_]*)", stripped)
                if match:
                    class_stack.append((match.group(1), indent))
                current_def = None
                continue

            def_match = re.match(r"def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", stripped)
            if def_match:
                current_def = (def_match.group(1), indent)
                continue

            if (
                current_def
                and indent <= current_def[1]
                and not stripped.startswith("#")
            ):
                current_def = None

            current_class = class_stack[-1][0] if class_stack else None
            template_name_match = template_name_pattern.search(stripped)
            if current_class and template_name_match:
                class_template[current_class] = template_name_match.group(1)

            render_match = render_pattern.search(
                stripped
            ) or template_response_pattern.search(stripped)
            if render_match:
                template_name = render_match.group(1)
                handler_name = current_def[0] if current_def else None
                results.append((handler_name, current_class, template_name))

        for class_name, template_name in class_template.items():
            results.append((class_name, class_name, template_name))

        return results

    def _link_template_js_chain(self, file_path: Path, source: str) -> None:
        """Links HTML templates to referenced JS/TS files and API handlers."""
        relative_path = str(file_path.relative_to(self.repo_path))
        template_node = (cs.NodeLabel.FILE, cs.KEY_PATH, relative_path)

        handler_names = self._extract_html_handlers(source)
        for handler in handler_names:
            handler_qn = self._find_best_handler_qn(handler, None)
            if not handler_qn:
                continue
            handler_type = self.function_registry.get(handler_qn)
            if not handler_type:
                continue
            self.ingestor.ensure_relationship_batch(
                template_node,
                cs.RelationshipType.USES_HANDLER,
                (handler_type.value, cs.KEY_QUALIFIED_NAME, handler_qn),
                {cs.KEY_RELATION_TYPE: "dom_event"},
            )

        for script_path in self._extract_script_sources(file_path, source):
            script_rel = self._resolve_asset_path(file_path.parent, script_path)
            if not script_rel:
                continue
            self.ingestor.ensure_relationship_batch(
                template_node,
                cs.RelationshipType.EMBEDS,
                (cs.NodeLabel.FILE, cs.KEY_PATH, script_rel),
                {cs.KEY_RELATION_TYPE: "script"},
            )
            self._link_js_ts_requests(Path(self.repo_path / script_rel), handler_names)

    @staticmethod
    def _extract_script_sources(file_path: Path, source: str) -> list[str]:
        """Extracts 'src' attributes from <script> tags."""
        if file_path.suffix.lower() not in {cs.EXT_HTML, cs.EXT_HTM}:
            return []
        script_pattern = re.compile(
            r"<script[^>]+src\s*=\s*['\"]([^'\"]+)['\"][^>]*>",
            re.IGNORECASE,
        )
        return [match.group(1) for match in script_pattern.finditer(source)]

    @staticmethod
    def _extract_html_handlers(source: str) -> list[str]:
        """Extracts inline JavaScript event handlers (onclick, onsubmit, etc.)."""
        handlers: list[str] = []
        handler_pattern = re.compile(
            r"\bon[a-zA-Z]+\s*=\s*['\"]([^'\"]+)['\"]",
            re.IGNORECASE,
        )
        for match in handler_pattern.finditer(source):
            expression = match.group(1).strip()
            name = expression.split("(")[0].split(";")[0].strip()
            if name:
                handlers.append(name)
        return list(dict.fromkeys(handlers))

    def _link_js_ts_requests(self, file_path: Path, handler_names: list[str]) -> None:
        """Links JS/TS files to the API endpoints they request (fetch/axios)."""
        if not file_path.exists():
            return
        try:
            source = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return

        module_qn = self._module_qn_for_path(file_path)
        endpoints = self._extract_js_requests(source)
        if not endpoints:
            return

        relative_path = str(file_path.relative_to(self.repo_path))
        for endpoint in endpoints:
            endpoint_qn = self._ensure_endpoint_node(endpoint, relative_path)
            linked = False
            for handler in handler_names:
                handler_qn = self._find_best_handler_qn(handler, None)
                if not handler_qn:
                    continue
                handler_type = self.function_registry.get(handler_qn)
                if not handler_type:
                    continue
                self.ingestor.ensure_relationship_batch(
                    (handler_type.value, cs.KEY_QUALIFIED_NAME, handler_qn),
                    cs.RelationshipType.REQUESTS_ENDPOINT,
                    (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn),
                    {cs.KEY_RELATION_TYPE: "http_request"},
                )
                linked = True
            if not linked:
                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
                    cs.RelationshipType.REQUESTS_ENDPOINT,
                    (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn),
                    {cs.KEY_RELATION_TYPE: "http_request"},
                )

    def _extract_js_requests(self, source: str) -> list[EndpointMatch]:
        """Extracts API requests from JS code (fetch, axios, GraphQL clients)."""
        endpoints: list[EndpointMatch] = []
        base_aliases = self._extract_js_base_urls(source)
        fetch_pattern = re.compile(
            r"fetch\s*\(\s*['\"]([^'\"]+)['\"](\s*,\s*\{([^}]*)\})?",
            re.IGNORECASE,
        )
        fetch_template_pattern = re.compile(
            r"fetch\s*\(\s*`([^`]+)`(\s*,\s*\{([^}]*)\})?",
            re.IGNORECASE,
        )
        method_pattern = re.compile(
            r"method\s*:\s*['\"](GET|POST|PUT|DELETE|PATCH)['\"]",
            re.IGNORECASE,
        )
        for match in fetch_pattern.finditer(source):
            raw_path = match.group(1)
            options = match.group(3) or ""
            method_match = method_pattern.search(options)
            method = (method_match.group(1) if method_match else "GET").upper()
            resolved = self._resolve_request_path(raw_path, None)
            endpoints.append(
                EndpointMatch(framework="http", method=method, path=resolved)
            )
        for match in fetch_template_pattern.finditer(source):
            raw_path = match.group(1)
            options = match.group(3) or ""
            method_match = method_pattern.search(options)
            method = (method_match.group(1) if method_match else "GET").upper()
            resolved = self._resolve_request_path(raw_path, None)
            endpoints.append(
                EndpointMatch(framework="http", method=method, path=resolved)
            )

        axios_pattern = re.compile(
            r"axios\.(get|post|put|delete|patch)\s*\(\s*['\"]([^'\"]+)['\"]",
            re.IGNORECASE,
        )
        for match in axios_pattern.finditer(source):
            method = match.group(1).upper()
            raw_path = match.group(2)
            resolved = self._resolve_request_path(raw_path, None)
            endpoints.append(
                EndpointMatch(framework="http", method=method, path=resolved)
            )

        for alias, base_url in base_aliases.items():
            alias_pattern = re.compile(
                rf"\b{re.escape(alias)}\.(get|post|put|delete|patch)\s*\(\s*['\"]([^'\"]+)['\"]",
                re.IGNORECASE,
            )
            for match in alias_pattern.finditer(source):
                method = match.group(1).upper()
                raw_path = match.group(2)
                resolved = self._resolve_request_path(raw_path, base_url)
                endpoints.append(
                    EndpointMatch(framework="http", method=method, path=resolved)
                )

        graphql_client_pattern = re.compile(
            r"new\s+GraphQLClient\s*\(\s*(['\"])(?P<url>[^'\"]+)\1",
            re.IGNORECASE,
        )
        for match in graphql_client_pattern.finditer(source):
            raw_url = match.group("url")
            resolved = self._resolve_request_path(raw_url, None)
            endpoints.append(
                EndpointMatch(framework="graphql", method="POST", path=resolved)
            )

        apollo_pattern = re.compile(
            r"ApolloClient\s*\(\s*\{[^}]*uri\s*:\s*(['\"])(?P<url>[^'\"]+)\1",
            re.IGNORECASE | re.DOTALL,
        )
        for match in apollo_pattern.finditer(source):
            raw_url = match.group("url")
            resolved = self._resolve_request_path(raw_url, None)
            endpoints.append(
                EndpointMatch(framework="graphql", method="POST", path=resolved)
            )

        urql_pattern = re.compile(
            r"createClient\s*\(\s*\{[^}]*url\s*:\s*(['\"])(?P<url>[^'\"]+)\1",
            re.IGNORECASE | re.DOTALL,
        )
        for match in urql_pattern.finditer(source):
            raw_url = match.group("url")
            resolved = self._resolve_request_path(raw_url, None)
            endpoints.append(
                EndpointMatch(framework="graphql", method="POST", path=resolved)
            )

        return endpoints

    def _link_csharp_di_consumers(
        self, file_path: Path, source: str, service_map: dict[str, str]
    ) -> None:
        """Links C# constructors to injected services/interfaces."""
        class_pattern = re.compile(r"class\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)")
        ctor_pattern = re.compile(
            r"(?:public|internal|protected|private)?\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<args>[^\)]*)\)"
        )
        primary_ctor_pattern = re.compile(
            r"class\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<args>[^\)]*)\)"
        )
        record_pattern = re.compile(
            r"record\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<args>[^\)]*)\)"
        )

        classes: list[str] = [m.group("name") for m in class_pattern.finditer(source)]
        if not classes:
            return

        constructor_sources = (
            list(ctor_pattern.finditer(source))
            + list(primary_ctor_pattern.finditer(source))
            + list(record_pattern.finditer(source))
        )

        for ctor in constructor_sources:
            class_name = ctor.group("name")
            if class_name not in classes:
                continue
            args = ctor.group("args")
            dependencies = []
            for token in args.split(","):
                part = token.strip()
                if not part:
                    continue
                type_name = self._normalize_csharp_type_name(part)
                if type_name:
                    dependencies.append(type_name)

            consumer_qn = self._find_best_controller_qn(class_name)
            if not consumer_qn:
                continue

            for dep in dependencies:
                provider_qn = service_map.get(dep) or self._find_best_qn_by_simple_name(
                    dep
                )
                if not provider_qn:
                    continue
                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.CLASS, cs.KEY_QUALIFIED_NAME, provider_qn),
                    cs.RelationshipType.PROVIDES_SERVICE,
                    (cs.NodeLabel.CLASS, cs.KEY_QUALIFIED_NAME, consumer_qn),
                    {cs.KEY_RELATION_TYPE: "dependency_injection"},
                )

    def _register_csharp_service(
        self,
        module_qn: str,
        type_name: str,
        lifetime: str,
        service_map: dict[str, str],
    ) -> None:
        """Registers a C# service in the local map and creates a graph node."""
        if not type_name:
            return
        qn = self._find_best_qn_by_simple_name(type_name)
        if not qn:
            return
        node_type = self.function_registry.get(qn)
        label = (
            cs.NodeLabel.INTERFACE
            if node_type == NodeType.INTERFACE
            else cs.NodeLabel.CLASS
        )
        self.ingestor.ensure_relationship_batch(
            (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
            cs.RelationshipType.REGISTERS_SERVICE,
            (label, cs.KEY_QUALIFIED_NAME, qn),
            {cs.KEY_RELATION_TYPE: "dependency_injection", cs.KEY_LIFETIME: lifetime},
        )
        service_map[type_name] = qn

    @staticmethod
    def _normalize_csharp_type_name(raw: str) -> str:
        """Normalizes C# type names (removes namespaces, generics)."""
        if not raw:
            return ""
        cleaned = raw.strip()
        cleaned = cleaned.split("=")[0].strip()
        cleaned = cleaned.replace("ref ", "").replace("out ", "").replace("in ", "")
        cleaned = cleaned.replace("?", "")
        cleaned = cleaned.split("[")[0]
        cleaned = cleaned.split("<")[0]
        cleaned = cleaned.split(".")[-1]
        return cleaned

    def _link_spring_di(self, file_path: Path, source: str) -> None:
        """Links Spring Boot @Autowired/@Inject dependencies."""
        provider_pattern = re.compile(
            r"@(Service|Component|Repository|Controller|RestController)\s*[\r\n]+\s*(?:public\s+)?class\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)",
            re.MULTILINE,
        )
        bean_pattern = re.compile(
            r"@Bean[\s\r\n]+(?:public\s+)?(?P<type>[A-Za-z_][A-Za-z0-9_<>\.]+)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(",
            re.MULTILINE,
        )
        autowired_field_pattern = re.compile(
            r"@Autowired\s*[\r\n]+\s*(?:private|protected|public)?\s*(?P<type>[A-Za-z_][A-Za-z0-9_<>\.]+)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)",
            re.MULTILINE,
        )
        class_pattern = re.compile(
            r"class\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)",
            re.MULTILINE,
        )
        ctor_pattern = re.compile(
            r"public\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<args>[^\)]*)\)",
            re.MULTILINE,
        )

        providers = {match.group("name") for match in provider_pattern.finditer(source)}
        providers.update(
            match.group("type").split("<")[0] for match in bean_pattern.finditer(source)
        )
        if not providers:
            return

        classes = [match.group("name") for match in class_pattern.finditer(source)]
        for ctor in ctor_pattern.finditer(source):
            class_name = ctor.group("name")
            if class_name not in classes:
                continue
            args = ctor.group("args")
            dependencies = [
                token.strip().split(" ")[0].split("<")[0].split(".")[-1]
                for token in args.split(",")
                if token.strip()
            ]
            self._link_provider_consumer("spring", class_name, dependencies, providers)

        for field in autowired_field_pattern.finditer(source):
            class_name = classes[0] if classes else None
            if not class_name:
                continue
            dep_type = field.group("type").split("<")[0].split(".")[-1]
            self._link_provider_consumer("spring", class_name, [dep_type], providers)

    def _link_nest_di(self, file_path: Path, source: str) -> None:
        """Links NestJS dependency injection (constructors)."""
        if file_path.suffix.lower() not in {cs.EXT_TS, cs.EXT_TSX}:
            return

        provider_pattern = re.compile(
            r"@Injectable\(.*?\)\s*export\s+class\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)",
            re.DOTALL,
        )
        controller_pattern = re.compile(
            r"@Controller\(.*?\)\s*export\s+class\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)",
            re.DOTALL,
        )
        module_pattern = re.compile(
            r"@Module\(\s*\{(?P<body>[^}]+)\}\s*\)",
            re.DOTALL,
        )
        providers_list_pattern = re.compile(
            r"providers\s*:\s*\[(?P<providers>[^\]]+)\]",
            re.DOTALL,
        )
        ctor_pattern = re.compile(
            r"constructor\s*\((?P<args>[^\)]*)\)",
            re.DOTALL,
        )

        providers = {match.group("name") for match in provider_pattern.finditer(source)}
        for module_match in module_pattern.finditer(source):
            providers_match = providers_list_pattern.search(module_match.group("body"))
            if not providers_match:
                continue
            for token in providers_match.group("providers").split(","):
                name = token.strip().split("{")[0].strip()
                if name:
                    providers.add(name)

        consumers = [
            match.group("name") for match in controller_pattern.finditer(source)
        ]
        if not providers or not consumers:
            return

        for consumer in consumers:
            for ctor in ctor_pattern.finditer(source):
                args = ctor.group("args")
                dependencies = [
                    token.strip().split(":")[-1].split("|")[0].strip()
                    for token in args.split(",")
                    if token.strip()
                ]
                self._link_provider_consumer(
                    "nestjs", consumer, dependencies, providers
                )

    def _link_provider_consumer(
        self,
        framework: str,
        consumer_name: str,
        dependencies: list[str],
        providers: set[str],
    ) -> None:
        """Creates a dependency relationship between consumer and provider."""
        consumer_qn = self._find_best_controller_qn(consumer_name)
        if not consumer_qn:
            return

        for dep in dependencies:
            dep_name = dep.split("<")[0].split(".")[-1]
            if dep_name not in providers:
                continue
            provider_qn = self._find_best_qn_by_simple_name(dep_name)
            if not provider_qn:
                continue
            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.CLASS, cs.KEY_QUALIFIED_NAME, provider_qn),
                cs.RelationshipType.PROVIDES_SERVICE,
                (cs.NodeLabel.CLASS, cs.KEY_QUALIFIED_NAME, consumer_qn),
                {cs.KEY_RELATION_TYPE: framework},
            )
