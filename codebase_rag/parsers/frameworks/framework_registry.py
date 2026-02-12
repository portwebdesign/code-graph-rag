from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codebase_rag.core import constants as cs

from .detectors.csharp_framework_detector import CSharpFrameworkDetector
from .detectors.go_framework_detector import GoFrameworkDetector
from .detectors.java_framework_detector import JavaFrameworkDetector
from .detectors.js_framework_detector import JsFrameworkDetector
from .detectors.php_framework_detector import PhpFrameworkDetector
from .detectors.python_framework_detector import PythonFrameworkDetector
from .detectors.ruby_framework_detector import RubyFrameworkDetector


@dataclass(frozen=True)
class FrameworkDetectionResult:
    framework_type: str | None
    metadata: dict[str, Any] | None


class FrameworkDetectorRegistry:
    def __init__(self, repo_path: Path | None = None) -> None:
        self.repo_path = repo_path
        self._python = PythonFrameworkDetector()
        self._java = JavaFrameworkDetector()
        self._ruby = RubyFrameworkDetector()
        self._js = JsFrameworkDetector()
        self._php = PhpFrameworkDetector()
        self._csharp = CSharpFrameworkDetector()
        self._go = GoFrameworkDetector()

    def detect_for_language(
        self,
        language: cs.SupportedLanguage | str,
        source_code: str,
        module_node: Any | None = None,
    ) -> FrameworkDetectionResult:
        lang = self._normalize_language(language)
        if not lang:
            return FrameworkDetectionResult(None, None)

        if lang == cs.SupportedLanguage.PYTHON:
            framework = self._python.detect_framework(module_node, source_code)
            if framework and framework.value != "none":
                metadata = self._python.get_framework_metadata(
                    framework, module_node, source_code
                )
                return FrameworkDetectionResult(framework.value, metadata)
            return FrameworkDetectionResult(None, None)

        if lang == cs.SupportedLanguage.JAVA:
            framework = self._java.detect_framework(source_code)
            if framework and framework.value != "none":
                return FrameworkDetectionResult(
                    framework.value, self._java.get_framework_metadata(source_code)
                )
            return FrameworkDetectionResult(None, None)

        if lang == cs.SupportedLanguage.RUBY:
            framework = self._ruby.detect_from_source(source_code)
            if framework and framework.value != "none":
                metadata = self._ruby.get_framework_metadata(source_code=source_code)
                return FrameworkDetectionResult(framework.value, metadata)
            return FrameworkDetectionResult(None, None)

        if lang in (cs.SupportedLanguage.JS, cs.SupportedLanguage.TS):
            framework = self._js.detect_from_source(source_code)
            if framework and framework.value != "none":
                return FrameworkDetectionResult(
                    framework.value, self._js.get_framework_metadata(source_code)
                )
            return FrameworkDetectionResult(None, None)

        if lang == cs.SupportedLanguage.PHP:
            framework = self._php.detect_from_source(source_code)
            if framework and framework.value != "none":
                return FrameworkDetectionResult(
                    framework.value, self._php.get_framework_metadata(source_code)
                )
            return FrameworkDetectionResult(None, None)

        if lang == cs.SupportedLanguage.CSHARP:
            framework = self._csharp.detect_from_source(source_code)
            if framework and framework.value != "none":
                return FrameworkDetectionResult(
                    framework.value, self._csharp.get_framework_metadata(source_code)
                )
            return FrameworkDetectionResult(None, None)

        if lang == cs.SupportedLanguage.GO:
            framework = self._go.detect_from_source(source_code)
            if framework and framework.value != "none":
                return FrameworkDetectionResult(
                    framework.value, self._go.get_framework_metadata(source_code)
                )
            return FrameworkDetectionResult(None, None)

        return FrameworkDetectionResult(None, None)

    def detect_repo(self) -> FrameworkDetectionResult:
        if not self.repo_path:
            return FrameworkDetectionResult(None, None)

        frameworks: list[str] = []
        metadata: dict[str, object] = {"repo_level": True}

        def extend_frameworks(meta: dict[str, object]) -> None:
            items = meta.get("frameworks")
            if isinstance(items, list):
                frameworks.extend([str(item) for item in items])

        php_meta = self._detect_php_repo_metadata()
        if php_meta["detected"]:
            extend_frameworks(php_meta)
        metadata["php"] = php_meta

        csharp_meta = self._detect_csharp_repo_metadata()
        if csharp_meta["detected"]:
            extend_frameworks(csharp_meta)
        metadata["csharp"] = csharp_meta

        go_meta = self._detect_go_repo_metadata()
        if go_meta["detected"]:
            extend_frameworks(go_meta)
        metadata["go"] = go_meta

        ruby_meta = self._detect_ruby_repo_metadata()
        if ruby_meta["detected"]:
            extend_frameworks(ruby_meta)
        metadata["ruby"] = ruby_meta

        python_meta = self._detect_python_repo_metadata()
        if python_meta["detected"]:
            extend_frameworks(python_meta)
        metadata["python"] = python_meta

        java_meta = self._detect_java_repo_metadata()
        if java_meta["detected"]:
            extend_frameworks(java_meta)
        metadata["java"] = java_meta

        js_meta = self._detect_js_repo_metadata()
        if js_meta["detected"]:
            extend_frameworks(js_meta)
        metadata["javascript"] = js_meta

        if frameworks:
            metadata["frameworks"] = frameworks
            metadata["detected"] = True
            return FrameworkDetectionResult(frameworks[0], metadata)

        metadata["frameworks"] = []
        metadata["detected"] = False
        return FrameworkDetectionResult(None, metadata)

    @staticmethod
    def _normalize_language(
        language: cs.SupportedLanguage | str,
    ) -> cs.SupportedLanguage | None:
        if isinstance(language, cs.SupportedLanguage):
            return language

        normalized = str(language).lower()
        alias_map = {
            "py": cs.SupportedLanguage.PYTHON,
            "python": cs.SupportedLanguage.PYTHON,
            "js": cs.SupportedLanguage.JS,
            "javascript": cs.SupportedLanguage.JS,
            "ts": cs.SupportedLanguage.TS,
            "typescript": cs.SupportedLanguage.TS,
            "java": cs.SupportedLanguage.JAVA,
            "ruby": cs.SupportedLanguage.RUBY,
            "php": cs.SupportedLanguage.PHP,
            "go": cs.SupportedLanguage.GO,
            "golang": cs.SupportedLanguage.GO,
            "c#": cs.SupportedLanguage.CSHARP,
            "csharp": cs.SupportedLanguage.CSHARP,
            "c-sharp": cs.SupportedLanguage.CSHARP,
        }
        return alias_map.get(normalized)

    def _detect_python_repo_metadata(self) -> dict[str, object]:
        if not self.repo_path:
            return {"detected": False, "frameworks": [], "files": [], "matches": []}
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
        if not self.repo_path:
            return {"detected": False, "frameworks": [], "files": [], "matches": []}
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
        if not self.repo_path:
            return {"detected": False, "frameworks": [], "files": [], "matches": []}
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
        if not self.repo_path:
            return {"detected": False, "frameworks": [], "files": [], "matches": []}
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
        if not self.repo_path:
            return {"detected": False, "frameworks": [], "files": [], "matches": []}
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
        if not self.repo_path:
            return {"detected": False, "frameworks": [], "files": [], "matches": []}
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
        if not self.repo_path:
            return {"detected": False, "frameworks": [], "files": [], "matches": []}
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
