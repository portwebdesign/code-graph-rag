import re
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class CSharpFrameworkType(Enum):
    ASPNET = "aspnet"
    ASPNET_CORE = "aspnet_core"
    NONE = "none"


@dataclass
class AspNetRoute:
    path: str
    method: str


@dataclass
class AspNetController:
    name: str


@dataclass
class DependencyInjectionRegistration:
    service: str
    implementation: str | None
    lifetime: str


class CSharpFrameworkDetector:
    ASPNET_IMPORTS = [
        "using Microsoft.AspNetCore",
        "using Microsoft.Extensions",
        "using Microsoft.EntityFrameworkCore",
        "using Microsoft.AspNetCore.Mvc",
    ]

    ASPNET_ATTRIBUTES = [
        "[ApiController]",
        "[Controller]",
        "[Route",
        "[HttpGet",
        "[HttpPost",
        "[HttpPut",
        "[HttpDelete",
        "[HttpPatch",
        "[HttpOptions",
        "[HttpHead",
    ]

    MINIMAL_API_MARKERS = [
        ".MapGet(",
        ".MapPost(",
        ".MapPut(",
        ".MapDelete(",
        ".MapPatch(",
        ".MapMethods(",
    ]

    DI_MARKERS = [
        "IServiceCollection",
        "builder.Services",
        "services.AddScoped(",
        "services.AddTransient(",
        "services.AddSingleton(",
        "AddDbContext<",
        "AddIdentity<",
    ]

    def detect_from_source(self, source_code: str) -> CSharpFrameworkType:
        if any(
            marker in source_code
            for marker in self.ASPNET_IMPORTS + self.ASPNET_ATTRIBUTES
        ):
            return CSharpFrameworkType.ASPNET_CORE
        if any(marker in source_code for marker in self.MINIMAL_API_MARKERS):
            return CSharpFrameworkType.ASPNET_CORE
        if any(marker in source_code for marker in self.DI_MARKERS):
            return CSharpFrameworkType.ASPNET_CORE
        return CSharpFrameworkType.NONE

    def detect_from_project(self, repo_root: Path | None = None) -> CSharpFrameworkType:
        if not repo_root:
            return CSharpFrameworkType.NONE

        for csproj in repo_root.rglob("*.csproj"):
            content = csproj.read_text(encoding="utf-8", errors="ignore")
            if "Microsoft.NET.Sdk.Web" in content or "Microsoft.AspNetCore" in content:
                return CSharpFrameworkType.ASPNET_CORE
        return CSharpFrameworkType.NONE

    def extract_controllers(self, source_code: str) -> list[AspNetController]:
        controllers: list[AspNetController] = []
        pattern = r"class\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*.*Controller"
        for match in re.finditer(pattern, source_code):
            controllers.append(AspNetController(name=match.group(1)))
        return controllers

    def extract_routes(self, source_code: str) -> list[AspNetRoute]:
        routes: list[AspNetRoute] = []
        attr_pattern = r"\[\s*Http(Get|Post|Put|Delete|Patch|Options|Head)\s*(?:\(\s*\"([^\"]*)\"\s*\))?\s*\]"
        for match in re.finditer(attr_pattern, source_code, re.IGNORECASE):
            method = match.group(1).upper()
            path = match.group(2) or ""
            routes.append(AspNetRoute(path=path, method=method))

        minimal_pattern = r"\.Map(Get|Post|Put|Delete|Patch)\s*\(\s*\"([^\"]*)\""
        for match in re.finditer(minimal_pattern, source_code, re.IGNORECASE):
            method = match.group(1).upper()
            path = match.group(2)
            routes.append(AspNetRoute(path=path, method=method))

        return routes

    def extract_dependency_injection(
        self, source_code: str
    ) -> list[DependencyInjectionRegistration]:
        registrations: list[DependencyInjectionRegistration] = []
        pattern = r"(?:builder\.Services|services)\.Add(Scoped|Transient|Singleton)\s*<\s*([^,>]+)\s*(?:,\s*([^>]+)\s*)?>"
        for match in re.finditer(pattern, source_code):
            lifetime = match.group(1)
            service = (match.group(2) or "").strip()
            implementation = match.group(3).strip() if match.group(3) else None
            registrations.append(
                DependencyInjectionRegistration(
                    service=service,
                    implementation=implementation,
                    lifetime=lifetime,
                )
            )
        return registrations

    def get_framework_metadata(self, source_code: str) -> dict[str, Any]:
        framework = self.detect_from_source(source_code)
        metadata: dict = {
            "framework_type": framework.value,
            "detected": framework != CSharpFrameworkType.NONE,
        }

        if framework != CSharpFrameworkType.NONE:
            metadata["controllers"] = self.extract_controllers(source_code)
            metadata["routes"] = self.extract_routes(source_code)
            metadata["dependency_injection"] = self.extract_dependency_injection(
                source_code
            )

        return self._to_serializable_metadata(metadata)

    def _to_serializable_metadata(self, value: Any) -> Any:
        if is_dataclass(value):
            return {
                k: self._to_serializable_metadata(v) for k, v in asdict(value).items()
            }
        if isinstance(value, dict):
            return {k: self._to_serializable_metadata(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._to_serializable_metadata(v) for v in value]
        if isinstance(value, tuple):
            return [self._to_serializable_metadata(v) for v in value]
        return value
