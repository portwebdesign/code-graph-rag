from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import cast


class FrameworkMetadataDetector:
    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path

    def detect(self) -> tuple[str | None, str | None]:
        frameworks: list[str] = []
        metadata: dict[str, object] = {"repo_level": True}

        php_meta = self._detect_php_repo_metadata()
        if php_meta["detected"]:
            frameworks.extend(cast(Iterable[str], php_meta["frameworks"]))
        metadata["php"] = php_meta

        csharp_meta = self._detect_csharp_repo_metadata()
        if csharp_meta["detected"]:
            frameworks.extend(cast(Iterable[str], csharp_meta["frameworks"]))
        metadata["csharp"] = csharp_meta

        go_meta = self._detect_go_repo_metadata()
        if go_meta["detected"]:
            frameworks.extend(cast(Iterable[str], go_meta["frameworks"]))
        metadata["go"] = go_meta

        ruby_meta = self._detect_ruby_repo_metadata()
        if ruby_meta["detected"]:
            frameworks.extend(cast(Iterable[str], ruby_meta["frameworks"]))
        metadata["ruby"] = ruby_meta

        python_meta = self._detect_python_repo_metadata()
        if python_meta["detected"]:
            frameworks.extend(cast(Iterable[str], python_meta["frameworks"]))
        metadata["python"] = python_meta

        java_meta = self._detect_java_repo_metadata()
        if java_meta["detected"]:
            frameworks.extend(cast(Iterable[str], java_meta["frameworks"]))
        metadata["java"] = java_meta

        js_meta = self._detect_js_repo_metadata()
        if js_meta["detected"]:
            frameworks.extend(cast(Iterable[str], js_meta["frameworks"]))
        metadata["javascript"] = js_meta

        if frameworks:
            metadata["frameworks"] = frameworks
            metadata["detected"] = True
            primary = frameworks[0]
            return primary, json.dumps(metadata, ensure_ascii=False)

        metadata["frameworks"] = []
        metadata["detected"] = False
        return None, json.dumps(metadata, ensure_ascii=False)

    def _detect_python_repo_metadata(self) -> dict[str, object]:
        candidates = [
            self.repo_path / "requirements.txt",
            self.repo_path / "pyproject.toml",
            self.repo_path / "setup.py",
        ]
        text_map = self._read_any_map(candidates)
        text = "\n".join(text_map.values()).lower()

        frameworks = self._find_matches(
            text,
            [
                "django",
                "djangorestframework",
                "rest_framework",
                "fastapi",
                "flask",
                "grpcio",
            ],
        )
        normalized: list[str] = []
        for entry in frameworks:
            if entry in ("djangorestframework", "rest_framework"):
                normalized.append("django_rest_framework")
            elif entry == "grpcio":
                normalized.append("grpc")
            else:
                normalized.append(entry)
        normalized = list(dict.fromkeys(normalized))
        return {
            "detected": bool(normalized),
            "frameworks": normalized,
            "files": list(text_map.keys()),
            "matches": frameworks,
        }

    def _detect_js_repo_metadata(self) -> dict[str, object]:
        package_json = self.repo_path / "package.json"
        if not package_json.exists():
            return {"detected": False, "frameworks": [], "files": [], "matches": []}

        text = package_json.read_text(encoding="utf-8", errors="ignore").lower()
        candidates = [
            "next",
            "nuxt",
            "nestjs",
            "express",
            "react",
            "vue",
            "angular",
            "svelte",
            "graphql",
            "apollo",
        ]
        frameworks = [
            candidate
            for candidate in candidates
            if f'"{candidate}' in text or f"{candidate}/" in text
        ]

        return {
            "detected": bool(frameworks),
            "frameworks": frameworks,
            "files": [str(package_json)],
            "matches": frameworks,
        }

    def _detect_java_repo_metadata(self) -> dict[str, object]:
        candidates = [
            self.repo_path / "pom.xml",
            self.repo_path / "build.gradle",
            self.repo_path / "build.gradle.kts",
        ]
        text_map = self._read_any_map(candidates)
        text = "\n".join(text_map.values()).lower()

        frameworks = self._find_matches(
            text,
            [
                "spring-boot",
                "org.springframework",
                "quarkus",
                "micronaut",
                "jakarta",
                "javax.ws.rs",
                "io.grpc",
            ],
        )
        normalized = []
        for entry in frameworks:
            if entry in ("spring-boot", "org.springframework"):
                normalized.append("spring_boot")
            elif entry in ("javax.ws.rs", "jakarta"):
                normalized.append("jakarta")
            elif entry == "io.grpc":
                normalized.append("grpc")
            else:
                normalized.append(entry)

        normalized = list(dict.fromkeys(normalized))
        return {
            "detected": bool(normalized),
            "frameworks": normalized,
            "files": list(text_map.keys()),
            "matches": frameworks,
        }

    def _detect_ruby_repo_metadata(self) -> dict[str, object]:
        gemfile = self.repo_path / "Gemfile"
        if not gemfile.exists():
            return {"detected": False, "frameworks": [], "files": [], "matches": []}

        text = gemfile.read_text(encoding="utf-8", errors="ignore").lower()
        frameworks = self._find_matches(text, ["rails", "sinatra", "hanami"])
        return {
            "detected": bool(frameworks),
            "frameworks": frameworks,
            "files": [str(gemfile)],
            "matches": frameworks,
        }

    def _detect_csharp_repo_metadata(self) -> dict[str, object]:
        csproj_files = list(self.repo_path.rglob("*.csproj"))
        if not csproj_files:
            return {"detected": False, "frameworks": [], "files": [], "matches": []}

        text_map = {
            str(path): path.read_text(encoding="utf-8", errors="ignore")
            for path in csproj_files
        }
        text = "\n".join(text_map.values()).lower()
        frameworks = []
        if "microsoft.net.sdk.web" in text or "microsoft.aspnetcore" in text:
            frameworks.append("aspnet_core")

        return {
            "detected": bool(frameworks),
            "frameworks": frameworks,
            "files": list(text_map.keys()),
            "matches": frameworks,
        }

    def _detect_go_repo_metadata(self) -> dict[str, object]:
        gomod = self.repo_path / "go.mod"
        if not gomod.exists():
            return {"detected": False, "frameworks": [], "files": [], "matches": []}

        text = gomod.read_text(encoding="utf-8", errors="ignore").lower()
        frameworks = []
        if "github.com/gin-gonic/gin" in text:
            frameworks.append("gin")
        if "github.com/labstack/echo" in text:
            frameworks.append("echo")
        if "github.com/gofiber/fiber" in text:
            frameworks.append("fiber")
        if "github.com/go-chi/chi" in text:
            frameworks.append("chi")
        if "github.com/gorilla/mux" in text:
            frameworks.append("gorilla")

        return {
            "detected": bool(frameworks),
            "frameworks": frameworks,
            "files": [str(gomod)],
            "matches": frameworks,
        }

    def _detect_php_repo_metadata(self) -> dict[str, object]:
        composer_json = self.repo_path / "composer.json"
        if not composer_json.exists():
            return {"detected": False, "frameworks": [], "files": [], "matches": []}

        text = composer_json.read_text(encoding="utf-8", errors="ignore").lower()
        frameworks = []
        if "laravel/framework" in text:
            frameworks.append("laravel")
        if "symfony/" in text:
            frameworks.append("symfony")

        return {
            "detected": bool(frameworks),
            "frameworks": frameworks,
            "files": [str(composer_json)],
            "matches": frameworks,
        }

    @staticmethod
    def _find_matches(text: str, keywords: list[str]) -> list[str]:
        return [key for key in keywords if key in text]

    @staticmethod
    def _read_any_map(paths: list[Path]) -> dict[str, str]:
        content: dict[str, str] = {}
        for path in paths:
            if path.exists():
                content[str(path)] = path.read_text(encoding="utf-8", errors="ignore")
        return content
