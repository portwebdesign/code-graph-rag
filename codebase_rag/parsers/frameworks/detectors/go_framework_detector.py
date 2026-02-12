import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class GoFrameworkType(Enum):
    GIN = "gin"
    ECHO = "echo"
    FIBER = "fiber"
    CHI = "chi"
    GORILLA = "gorilla"
    NONE = "none"


@dataclass
class GoRoute:
    path: str
    method: str
    handler: str | None = None


@dataclass
class GoMiddleware:
    name: str


class GoFrameworkDetector:
    IMPORT_MARKERS = {
        GoFrameworkType.GIN: "github.com/gin-gonic/gin",
        GoFrameworkType.ECHO: "github.com/labstack/echo",
        GoFrameworkType.FIBER: "github.com/gofiber/fiber",
        GoFrameworkType.CHI: "github.com/go-chi/chi",
        GoFrameworkType.GORILLA: "github.com/gorilla/mux",
    }

    def detect_from_source(self, source_code: str) -> GoFrameworkType:
        for framework, marker in self.IMPORT_MARKERS.items():
            if marker in source_code:
                return framework
        return GoFrameworkType.NONE

    def detect_from_project(self, repo_root: Path | None = None) -> GoFrameworkType:
        if not repo_root:
            return GoFrameworkType.NONE

        gomod = repo_root / "go.mod"
        if not gomod.exists():
            return GoFrameworkType.NONE

        content = gomod.read_text(encoding="utf-8", errors="ignore")
        for framework, marker in self.IMPORT_MARKERS.items():
            if marker in content:
                return framework
        return GoFrameworkType.NONE

    def extract_routes(
        self, source_code: str, framework: GoFrameworkType
    ) -> list[GoRoute]:
        routes: list[GoRoute] = []

        if framework in {
            GoFrameworkType.GIN,
            GoFrameworkType.ECHO,
            GoFrameworkType.FIBER,
            GoFrameworkType.CHI,
        }:
            pattern = r"\.([A-Z]+)\s*\(\s*\"([^\"]+)\"(?:\s*,\s*([A-Za-z_][\w\.]*))?"
            for match in re.finditer(pattern, source_code):
                method = match.group(1).upper()
                path = match.group(2)
                handler = match.group(3)
                routes.append(GoRoute(path=path, method=method, handler=handler))

        if framework == GoFrameworkType.GORILLA:
            pattern = r"HandleFunc\s*\(\s*\"([^\"]+)\"\s*,\s*([A-Za-z_][\w\.]*)[\s\S]*?\.Methods\s*\(\s*\"([^\"]+)\""
            for match in re.finditer(pattern, source_code):
                path = match.group(1)
                handler = match.group(2)
                method = match.group(3).upper()
                routes.append(GoRoute(path=path, method=method, handler=handler))

        return routes

    def extract_middleware(self, source_code: str) -> list[GoMiddleware]:
        middleware: list[GoMiddleware] = []
        pattern = r"\.Use\s*\(\s*([A-Za-z_][\w]*)"
        for match in re.finditer(pattern, source_code):
            name = match.group(1)
            if name:
                middleware.append(GoMiddleware(name=name))
        return middleware

    def get_framework_metadata(self, source_code: str) -> dict:
        framework = self.detect_from_source(source_code)
        metadata: dict = {
            "framework_type": framework.value,
            "detected": framework != GoFrameworkType.NONE,
        }

        if framework != GoFrameworkType.NONE:
            metadata["routes"] = self.extract_routes(source_code, framework)
            metadata["middleware"] = self.extract_middleware(source_code)

        return metadata
