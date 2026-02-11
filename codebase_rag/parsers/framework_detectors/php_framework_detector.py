import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class PhpFrameworkType(Enum):
    """Supported PHP frameworks."""

    LARAVEL = "laravel"
    SYMFONY = "symfony"
    NONE = "none"


@dataclass
class LaravelRoute:
    """Laravel route definition."""

    path: str
    method: str


@dataclass
class LaravelController:
    """Laravel controller definition."""

    name: str


@dataclass
class LaravelModel:
    """Laravel model definition."""

    name: str


@dataclass
class LaravelMiddleware:
    """Laravel middleware definition."""

    name: str


@dataclass
class LaravelServiceProvider:
    """Laravel service provider definition."""

    name: str


@dataclass
class SymfonyRoute:
    """Symfony route definition."""

    path: str
    methods: list[str] = field(default_factory=list)


class PhpFrameworkDetector:
    """Detect PHP frameworks and extract framework-specific metadata."""

    LARAVEL_INDICATORS = [
        "Illuminate\\\\",
        "Laravel\\\\",
        "Route::",
        "artisan",
        "App\\\\Http\\\\Controllers",
    ]

    SYMFONY_INDICATORS = [
        "Symfony\\\\",
        "Symfony\\\\Component\\\\",
        "@Route",
        "#[Route",
    ]

    def detect_from_source(self, source_code: str) -> PhpFrameworkType:
        if any(indicator in source_code for indicator in self.LARAVEL_INDICATORS):
            return PhpFrameworkType.LARAVEL
        if any(indicator in source_code for indicator in self.SYMFONY_INDICATORS):
            return PhpFrameworkType.SYMFONY
        return PhpFrameworkType.NONE

    def detect_from_project(self, repo_root: Path | None = None) -> PhpFrameworkType:
        if not repo_root:
            return PhpFrameworkType.NONE

        composer = Path(repo_root) / "composer.json"
        if not composer.exists():
            return PhpFrameworkType.NONE

        content = composer.read_text(encoding="utf-8", errors="ignore")
        if "laravel/framework" in content:
            return PhpFrameworkType.LARAVEL
        if "symfony/" in content:
            return PhpFrameworkType.SYMFONY
        return PhpFrameworkType.NONE

    def extract_laravel_routes(self, source_code: str) -> list[LaravelRoute]:
        routes: list[LaravelRoute] = []
        pattern = (
            r"Route::(get|post|put|patch|delete|options|any)\s*\(\s*['\"]([^'\"]+)['\"]"
        )
        for match in re.finditer(pattern, source_code, re.IGNORECASE):
            method = match.group(1).upper()
            path = match.group(2)
            routes.append(LaravelRoute(path=path, method=method))
        return routes

    def extract_laravel_controllers(self, source_code: str) -> list[LaravelController]:
        controllers: list[LaravelController] = []
        pattern = r"class\s+(\w+)\s+extends\s+Controller"
        for match in re.finditer(pattern, source_code):
            name = match.group(1)
            if name:
                controllers.append(LaravelController(name=name))
        return controllers

    def extract_laravel_models(self, source_code: str) -> list[LaravelModel]:
        models: list[LaravelModel] = []
        pattern = r"class\s+(\w+)\s+extends\s+Model"
        for match in re.finditer(pattern, source_code):
            name = match.group(1)
            if name:
                models.append(LaravelModel(name=name))
        return models

    def extract_laravel_middleware(self, source_code: str) -> list[LaravelMiddleware]:
        middleware: list[LaravelMiddleware] = []
        pattern = r"class\s+(\w+)\s+(?:implements|extends)\s+Middleware"
        for match in re.finditer(pattern, source_code):
            name = match.group(1)
            if name:
                middleware.append(LaravelMiddleware(name=name))
        return middleware

    def extract_laravel_providers(
        self, source_code: str
    ) -> list[LaravelServiceProvider]:
        providers: list[LaravelServiceProvider] = []
        pattern = r"class\s+(\w+)\s+extends\s+ServiceProvider"
        for match in re.finditer(pattern, source_code):
            name = match.group(1)
            if name:
                providers.append(LaravelServiceProvider(name=name))
        return providers

    def extract_symfony_routes(self, source_code: str) -> list[SymfonyRoute]:
        routes: list[SymfonyRoute] = []

        anno_pattern = r"@Route\(\s*['\"]([^'\"]+)['\"](?:[^)]*methods=\{([^}]*)\})?"
        for match in re.finditer(anno_pattern, source_code, re.IGNORECASE):
            path = match.group(1)
            methods_raw = match.group(2) or ""
            methods = [
                m.strip().strip("'\"") for m in methods_raw.split(",") if m.strip()
            ]
            routes.append(SymfonyRoute(path=path, methods=methods))

        attr_pattern = (
            r"#\[Route\(\s*['\"]([^'\"]+)['\"](?:[^\]]*methods:\s*\[([^\]]*)\])?"
        )
        for match in re.finditer(attr_pattern, source_code, re.IGNORECASE):
            path = match.group(1)
            methods_raw = match.group(2) or ""
            methods = [
                m.strip().strip("'\"") for m in methods_raw.split(",") if m.strip()
            ]
            routes.append(SymfonyRoute(path=path, methods=methods))

        return routes

    def get_framework_metadata(self, source_code: str) -> dict:
        framework = self.detect_from_source(source_code)
        metadata: dict = {
            "framework_type": framework.value,
            "detected": framework != PhpFrameworkType.NONE,
        }

        if framework == PhpFrameworkType.LARAVEL:
            metadata["routes"] = self.extract_laravel_routes(source_code)
            metadata["controllers"] = self.extract_laravel_controllers(source_code)
            metadata["models"] = self.extract_laravel_models(source_code)
            metadata["middleware"] = self.extract_laravel_middleware(source_code)
            metadata["providers"] = self.extract_laravel_providers(source_code)
        elif framework == PhpFrameworkType.SYMFONY:
            metadata["routes"] = self.extract_symfony_routes(source_code)

        return metadata
