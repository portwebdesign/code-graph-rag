import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


class YAMLNodeType(Enum):
    """YAML node types."""

    MAPPING = "mapping"
    SEQUENCE = "sequence"
    SCALAR = "scalar"
    BLOCK = "block"
    FLOW = "flow"


@dataclass
class YAMLPair:
    """YAML key-value pair."""

    key: str
    value: Any
    line_number: int | None = None
    depth: int = 0
    value_type: str = "scalar"


@dataclass
class YAMLDocument:
    """Complete YAML document structure."""

    pairs: list[YAMLPair] = field(default_factory=list)
    arrays: list[str] = field(default_factory=list)
    nested_keys: list[str] = field(default_factory=list)
    max_depth: int = 0
    raw_content: str | None = None


@dataclass
class KubernetesResource:
    """Kubernetes resource from YAML."""

    api_version: str
    kind: str
    name: str
    namespace: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    annotations: dict[str, str] = field(default_factory=dict)
    spec_type: str | None = None
    line_number: int | None = None


@dataclass
class DockerComposeService:
    """Docker Compose service definition."""

    name: str
    image: str | None = None
    build: str | None = None
    ports: list[str] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=dict)
    volumes: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    networks: list[str] = field(default_factory=list)
    line_number: int | None = None


@dataclass
class JSONSchema:
    """JSON Schema information."""

    title: str | None = None
    description: str | None = None
    required_properties: list[str] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)
    type: str | None = None


@dataclass
class PackageJsonInfo:
    """Information from package.json."""

    name: str
    version: str
    description: str | None = None
    main: str | None = None
    dependencies: dict[str, str] = field(default_factory=dict)
    dev_dependencies: dict[str, str] = field(default_factory=dict)
    scripts: dict[str, str] = field(default_factory=dict)
    keywords: list[str] = field(default_factory=list)


class YAMLParserMixin:
    """YAML parsing logic."""

    def extract_yaml_structure(
        self, content: str, file_path: str | None = None
    ) -> YAMLDocument:
        """
        Extract YAML structure.

        Args:
            content: YAML content
            file_path: Optional file path

        Returns:
            YAMLDocument with structure information
        """
        document = YAMLDocument(raw_content=content)

        try:
            data = yaml.safe_load(content) if content.strip() else {}
        except yaml.YAMLError:
            data = {}

        if isinstance(data, dict):
            document.pairs, document.nested_keys, document.max_depth = (
                self._extract_pairs(data, 0)
            )
            document.arrays = self._extract_arrays(data)

        return document

    def _extract_pairs(self, obj: Any, depth: int, prefix: str = "") -> tuple:
        """Recursively extract key-value pairs from YAML."""
        pairs = []
        nested_keys = []
        max_depth = depth

        if not isinstance(obj, dict):
            return pairs, nested_keys, max_depth

        for key, value in obj.items():
            full_key = f"{prefix}.{key}" if prefix else key
            nested_keys.append(full_key)

            value_type = "scalar"
            if isinstance(value, dict):
                value_type = "mapping"
            elif isinstance(value, list):
                value_type = "sequence"

            pairs.append(
                YAMLPair(key=key, value=value, depth=depth, value_type=value_type)
            )

            if isinstance(value, dict):
                nested_pairs, nested_keys_list, nested_depth = self._extract_pairs(
                    value, depth + 1, full_key
                )
                pairs.extend(nested_pairs)
                nested_keys.extend(nested_keys_list)
                max_depth = max(max_depth, nested_depth)
            elif isinstance(value, list) and value and isinstance(value[0], dict):
                for item in value:
                    if isinstance(item, dict):
                        nested_pairs, nested_keys_list, nested_depth = (
                            self._extract_pairs(item, depth + 1, full_key)
                        )
                        pairs.extend(nested_pairs)
                        nested_keys.extend(nested_keys_list)
                        max_depth = max(max_depth, nested_depth)

        return pairs, nested_keys, max_depth

    def _extract_arrays(self, obj: Any, prefix: str = "") -> list[str]:
        """Extract array keys from YAML."""
        arrays = []

        if isinstance(obj, dict):
            for key, value in obj.items():
                full_key = f"{prefix}.{key}" if prefix else key
                if isinstance(value, list):
                    arrays.append(full_key)
                elif isinstance(value, dict):
                    arrays.extend(self._extract_arrays(value, full_key))

        return arrays

    def extract_k8s_resources(self, content: str) -> list[KubernetesResource]:
        """
        Extract Kubernetes resources from YAML.

        Args:
            content: YAML content

        Returns:
            List of KubernetesResource objects
        """
        resources = []

        documents = content.split("---")

        for doc in documents:
            try:
                data = yaml.safe_load(doc)
                if not isinstance(data, dict):
                    continue

                if "apiVersion" in data and "kind" in data:
                    metadata = data.get("metadata", {})
                    resource = KubernetesResource(
                        api_version=data.get("apiVersion"),
                        kind=data.get("kind"),
                        name=metadata.get("name", "unknown"),
                        namespace=metadata.get("namespace"),
                        labels=metadata.get("labels", {}),
                        annotations=metadata.get("annotations", {}),
                        spec_type=(
                            list(data.get("spec", {}).keys())[0]
                            if data.get("spec")
                            else None
                        ),
                    )
                    resources.append(resource)
            except yaml.YAMLError:
                continue

        return resources

    def extract_docker_compose(self, content: str) -> list[DockerComposeService]:
        """
        Extract Docker Compose services from YAML.

        Args:
            content: docker-compose.yml content

        Returns:
            List of DockerComposeService objects
        """
        services = []

        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError:
            return services

        services_section = data.get("services", {})

        for service_name, service_config in services_section.items():
            if not isinstance(service_config, dict):
                continue

            service = DockerComposeService(
                name=service_name,
                image=service_config.get("image"),
                build=service_config.get("build"),
                ports=service_config.get("ports", []),
                environment=self._flatten_env(service_config.get("environment", {})),
                volumes=service_config.get("volumes", []),
                depends_on=self._flatten_depends_on(
                    service_config.get("depends_on", [])
                ),
                networks=service_config.get("networks", []),
            )
            services.append(service)

        return services

    def _flatten_env(self, env: dict | list) -> dict[str, str]:
        """Flatten environment variables."""
        result = {}

        if isinstance(env, dict):
            return {k: str(v) for k, v in env.items()}
        elif isinstance(env, list):
            for item in env:
                if "=" in str(item):
                    key, value = str(item).split("=", 1)
                    result[key] = value

        return result

    def _flatten_depends_on(self, depends_on: list | dict) -> list[str]:
        """Flatten depends_on section."""
        if isinstance(depends_on, list):
            return depends_on
        elif isinstance(depends_on, dict):
            return list(depends_on.keys())
        return []


class JSONParserMixin:
    """JSON parsing logic."""

    def extract_json_structure(
        self, content: str, file_path: str | None = None
    ) -> dict[str, Any]:
        """
        Extract JSON structure.

        Args:
            content: JSON content
            file_path: Optional file path

        Returns:
            Dictionary with JSON structure analysis
        """
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON", "raw_content": content}

        return {
            "type": type(data).__name__,
            "data": data,
            "keys": list(data.keys()) if isinstance(data, dict) else None,
            "length": len(data) if isinstance(data, list | dict) else None,
        }

    def extract_package_json(self, content: str) -> PackageJsonInfo:
        """
        Extract information from package.json.

        Args:
            content: package.json content

        Returns:
            PackageJsonInfo with extracted information
        """
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return PackageJsonInfo(name="", version="")

        return PackageJsonInfo(
            name=data.get("name", ""),
            version=data.get("version", ""),
            description=data.get("description"),
            main=data.get("main"),
            dependencies=data.get("dependencies", {}),
            dev_dependencies=data.get("devDependencies", {}),
            scripts=data.get("scripts", {}),
            keywords=data.get("keywords", []),
        )

    def extract_json_schema(self, content: str) -> JSONSchema:
        """
        Extract JSON Schema information.

        Args:
            content: JSON Schema content

        Returns:
            JSONSchema with extracted information
        """
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return JSONSchema()

        required_props = data.get("required", [])
        properties = {
            name: prop.get("type", "unknown")
            for name, prop in data.get("properties", {}).items()
        }

        return JSONSchema(
            title=data.get("title"),
            description=data.get("description"),
            required_properties=required_props,
            properties=properties,
            type=data.get("type"),
        )

    def extract_json_imports(
        self, content: str, file_type: str | None = None
    ) -> list[dict[str, str]]:
        """
        Extract import-like dependencies from JSON.

        Args:
            content: JSON content
            file_type: Type of JSON file (package, tsconfig, etc.)

        Returns:
            List of dependencies/imports
        """
        imports = []

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return imports

        for dep_key in ["dependencies", "devDependencies", "peerDependencies"]:
            if dep_key in data:
                for package, version in data[dep_key].items():
                    imports.append(
                        {
                            "type": "dependency",
                            "package": package,
                            "version": version,
                            "category": dep_key.replace("Dependencies", ""),
                        }
                    )

        if "extends" in data:
            imports.append(
                {
                    "type": "extends",
                    "extends": data["extends"],
                }
            )

        if "compilerOptions" in data and "paths" in data["compilerOptions"]:
            for path_alias, path_list in data["compilerOptions"]["paths"].items():
                imports.append(
                    {
                        "type": "path_alias",
                        "alias": path_alias,
                        "paths": path_list,
                    }
                )

        return imports


class ConfigParserMixin(YAMLParserMixin, JSONParserMixin):
    """Combined YAML/JSON configuration parsing."""

    def detect_config_type(self, file_path: str) -> str | None:
        """
        Detect configuration file type.

        Args:
            file_path: Path to configuration file

        Returns:
            Configuration type string or None
        """
        path = Path(file_path)
        name = path.name

        if "docker-compose" in name:
            return "docker-compose"

        if any(
            k in name
            for k in [
                "deployment",
                "service",
                "pod",
                "statefulset",
                "k8s",
                "kubernetes",
            ]
        ):
            return "kubernetes"

        if name == "package.json":
            return "package.json"

        if "tsconfig" in name:
            return "tsconfig"

        if any(
            ci in name
            for ci in ["gitlab-ci", "github", "jenkins", "circleci", "travis"]
        ):
            return "ci-config"

        if name in ["docker-compose.yml", "docker-compose.yaml"]:
            return "docker-compose"

        return None

    def parse_config_file(self, file_path: str) -> dict[str, Any]:
        """
        Parse any supported configuration file.

        Args:
            file_path: Path to configuration file

        Returns:
            Parsed configuration with metadata
        """
        path = Path(file_path)
        content = path.read_text(encoding="utf-8", errors="ignore")
        config_type = self.detect_config_type(file_path)

        result = {
            "file_path": str(file_path),
            "file_type": path.suffix,
            "config_type": config_type,
            "size_bytes": len(content),
        }

        if path.suffix in [".json"]:
            result["content"] = json.loads(content) if content.strip() else {}

            if config_type == "package.json":
                result["package_info"] = self.extract_package_json(content)
            elif "tsconfig" in str(file_path):
                result["schema_info"] = self.extract_json_schema(content)

        elif path.suffix in [".yaml", ".yml"]:
            result["content"] = yaml.safe_load(content) if content.strip() else {}

            if config_type == "docker-compose":
                result["services"] = self.extract_docker_compose(content)
            elif config_type == "kubernetes":
                result["resources"] = self.extract_k8s_resources(content)

        return result
