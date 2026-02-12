import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class RubyFrameworkType(Enum):
    """Supported Ruby frameworks."""

    RAILS = "rails"
    SINATRA = "sinatra"
    HANAMI = "hanami"
    NONE = "none"


@dataclass
class RailsRoute:
    """Rails route definition."""

    path: str
    controller: str
    action: str
    http_method: str | None = None
    as_name: str | None = None


@dataclass
class RailsModel:
    """Rails Active Record model."""

    class_name: str
    table_name: str | None = None
    associations: list[str] = field(default_factory=list)
    validations: list[str] = field(default_factory=list)
    callbacks: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)


@dataclass
class RailsController:
    """Rails controller information."""

    class_name: str
    actions: list[str] = field(default_factory=list)
    before_actions: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)


class RubyFrameworkDetector:
    """Detect Ruby frameworks from project structure and source code.

    Detection:
        - Rails: config/routes.rb, app/ directory structure, Gemfile
        - Sinatra: Sinatra app structure, route definitions
        - Hanami: Hanami configuration files

    Example:
        detector = RubyFrameworkDetector()
        framework = detector.detect_from_project(repo_root)
        if framework == RubyFrameworkType.RAILS:
            routes = detector.extract_rails_routes(routes_file)
    """

    RAILS_INDICATORS = {
        "files": ["config/routes.rb", "config/database.yml", "Gemfile"],
        "directories": ["app/controllers", "app/models", "app/views", "config"],
        "gems": ["rails", "activerecord", "actionpack", "actionview"],
    }

    SINATRA_INDICATORS = {
        "gems": ["sinatra"],
        "patterns": ["from sinatra import", "require 'sinatra'"],
    }

    def __init__(self, repo_root: Path | None = None):
        """Initialize Ruby framework detector.

        Args:
            repo_root: Optional repository root path for project-level detection
        """
        self.repo_root = repo_root

    def detect_from_project(self, repo_root: Path | None = None) -> RubyFrameworkType:
        """Detect framework from project structure.

        Args:
            repo_root: Repository root directory path

        Returns:
            RubyFrameworkType detected framework

        Example:
            framework = detector.detect_from_project(Path("/path/to/project"))
            print(f"Detected: {framework.value}")
        """
        root = repo_root or self.repo_root
        if not root:
            return RubyFrameworkType.NONE

        root = Path(root)

        if self._check_rails_structure(root):
            return RubyFrameworkType.RAILS

        if self._check_sinatra_structure(root):
            return RubyFrameworkType.SINATRA

        if self._check_hanami_structure(root):
            return RubyFrameworkType.HANAMI

        return RubyFrameworkType.NONE

    def detect_from_source(self, source_code: str) -> RubyFrameworkType:
        """Detect framework from Ruby source code.

        Args:
            source_code: Ruby source code as string

        Returns:
            RubyFrameworkType detected framework

        Example:
            framework = detector.detect_from_source(ruby_code)
        """
        if (
            "ActiveRecord::Base" in source_code
            or "ApplicationController" in source_code
        ):
            return RubyFrameworkType.RAILS

        if "require 'sinatra'" in source_code or "require 'sinatra" in source_code:
            return RubyFrameworkType.SINATRA

        if "Hanami.app" in source_code or "require 'hanami" in source_code:
            return RubyFrameworkType.HANAMI

        return RubyFrameworkType.NONE

    def extract_rails_routes(self, routes_file: Path) -> list[RailsRoute]:
        """Extract routes from Rails routes.rb file.

        Args:
            routes_file: Path to config/routes.rb

        Returns:
            List of RailsRoute objects

        Example:
            routes = detector.extract_rails_routes(Path("config/routes.rb"))
            for route in routes:
                print(f"{route.http_method} {route.path}")
        """
        routes = []

        if not routes_file.exists():
            return routes

        content = routes_file.read_text()
        prefixes: list[str] = []
        lines = content.splitlines()

        route_pattern = r'(get|post|put|delete|patch)\s+["\']([^"\']+)["\']'
        root_pattern = r'root\s+["\']([^"\']+)["\']'
        resources_pattern = r"(resources|resource)\s+:(\w+)"

        for line in lines:
            line_strip = line.strip()
            namespace_match = re.match(r"namespace\s+:?(\w+)", line_strip)
            if namespace_match:
                prefixes.append(self._normalize_route_prefix(namespace_match.group(1)))
                continue

            scope_match = re.match(r'scope\s+["\']([^"\']+)["\']', line_strip)
            if scope_match:
                prefixes.append(self._normalize_route_prefix(scope_match.group(1)))
                continue

            if line_strip == "end" and prefixes:
                prefixes.pop()
                continue

            match = re.search(route_pattern, line_strip, re.IGNORECASE)
            if match:
                http_method = match.group(1).lower()
                path = self._apply_route_prefix(match.group(2), prefixes)
                controller, action = self._extract_controller_action(line_strip)
                as_name = self._extract_route_name(line_strip)
                routes.append(
                    RailsRoute(
                        path=path,
                        controller=controller,
                        action=action,
                        http_method=http_method,
                        as_name=as_name,
                    )
                )
                continue

            root_match = re.search(root_pattern, line_strip, re.IGNORECASE)
            if root_match:
                controller, action = self._extract_controller_action(line_strip)
                as_name = self._extract_route_name(line_strip)
                routes.append(
                    RailsRoute(
                        path=self._apply_route_prefix("/", prefixes),
                        controller=controller,
                        action=action,
                        http_method="get",
                        as_name=as_name,
                    )
                )
                continue

            resources_match = re.search(resources_pattern, line_strip, re.IGNORECASE)
            if resources_match:
                resource = resources_match.group(2)
                resource_path = self._apply_route_prefix(f"/{resource}", prefixes)
                resource_name = self._extract_route_name(line_strip) or resource
                for action, method in [
                    ("index", "GET"),
                    ("show", "GET"),
                    ("new", "GET"),
                    ("create", "POST"),
                    ("edit", "GET"),
                    ("update", "PUT"),
                    ("destroy", "DELETE"),
                ]:
                    routes.append(
                        RailsRoute(
                            path=resource_path,
                            controller=resource,
                            action=action,
                            http_method=method,
                            as_name=resource_name,
                        )
                    )

        return routes

    def _extract_route_name(self, line: str) -> str | None:
        match = re.search(r'as:\s*["\']([^"\']+)["\']', line)
        if match:
            return match.group(1)
        match = re.search(r"as:\s*:(\w+)", line)
        if match:
            return match.group(1)
        return None

    def _extract_controller_action(self, line: str) -> tuple[str, str]:
        to_match = re.search(r'to:\s*["\']([^"\']+)["\']', line)
        if to_match and "#" in to_match.group(1):
            controller, action = to_match.group(1).split("#", 1)
            return controller, action

        controller_match = re.search(r'controller:\s*["\']([^"\']+)["\']', line)
        action_match = re.search(r'action:\s*["\']([^"\']+)["\']', line)
        controller = controller_match.group(1) if controller_match else ""
        action = action_match.group(1) if action_match else ""
        return controller, action

    def _normalize_route_prefix(self, prefix: str) -> str:
        if not prefix.startswith("/"):
            return f"/{prefix}"
        return prefix

    def _apply_route_prefix(self, path: str, prefixes: list[str]) -> str:
        normalized = path if path.startswith("/") else f"/{path}"
        if not prefixes:
            return normalized
        return "".join(prefixes) + normalized

    def extract_rails_models(self, source_code: str) -> list[RailsModel]:
        """Extract Rails Active Record models.

        Args:
            source_code: Ruby source code

        Returns:
            List of RailsModel objects

        Example:
            models = detector.extract_rails_models(source_code)
            for model in models:
                print(f"Model: {model.class_name}")
                for assoc in model.associations:
                    print(f"  Association: {assoc}")
        """
        models = []

        if (
            "< ApplicationRecord" not in source_code
            and "< ActiveRecord::Base" not in source_code
        ):
            return models

        class_pattern = r"class\s+(\w+)\s*<\s*(?:ApplicationRecord|ActiveRecord::Base)"
        class_match = re.search(class_pattern, source_code)
        if not class_match:
            return models

        class_name = class_match.group(1)

        table_name = None
        table_pattern = r'self\.table_name\s*=\s*["\']([^"\']+)["\']'
        table_match = re.search(table_pattern, source_code)
        if table_match:
            table_name = table_match.group(1)

        associations = self._extract_associations(source_code)

        validations = self._extract_validations(source_code)

        callbacks = self._extract_callbacks(source_code)

        scopes = self._extract_scopes(source_code)

        models.append(
            RailsModel(
                class_name=class_name,
                table_name=table_name,
                associations=associations,
                validations=validations,
                callbacks=callbacks,
                scopes=scopes,
            )
        )

        return models

    def extract_rails_controllers(self, source_code: str) -> list[RailsController]:
        """Extract Rails controller information.

        Args:
            source_code: Ruby source code

        Returns:
            List of RailsController objects

        Example:
            controllers = detector.extract_rails_controllers(source_code)
            for controller in controllers:
                print(f"Controller: {controller.class_name}")
                print(f"Actions: {', '.join(controller.actions)}")
        """
        controllers = []

        if "ApplicationController" not in source_code:
            return controllers

        class_pattern = r"class\s+(\w+)\s*<\s*ApplicationController"
        class_match = re.search(class_pattern, source_code)
        if not class_match:
            return controllers

        class_name = class_match.group(1)

        action_pattern = r"def\s+(\w+)"
        actions = [match.group(1) for match in re.finditer(action_pattern, source_code)]

        before_action_pattern = r"before_action\s+(?::(\w+)|{\s*([^}]+)\s*})"
        before_actions = [
            match.group(1) or match.group(2)
            for match in re.finditer(before_action_pattern, source_code)
        ]

        controllers.append(
            RailsController(
                class_name=class_name,
                actions=actions,
                before_actions=before_actions,
            )
        )

        return controllers

    def _check_rails_structure(self, repo_root: Path) -> bool:
        """Check if directory structure indicates Rails project."""
        rails_files = [
            repo_root / "config" / "routes.rb",
            repo_root / "Gemfile",
            repo_root / "app" / "controllers",
            repo_root / "app" / "models",
        ]

        existing = sum(1 for f in rails_files if f.exists())
        return existing >= 2

    def _check_sinatra_structure(self, repo_root: Path) -> bool:
        """Check if directory structure indicates Sinatra project."""
        gemfile = repo_root / "Gemfile"
        if gemfile.exists():
            content = gemfile.read_text()
            if "sinatra" in content:
                return True

        for pattern in ["app.rb", "main.rb", "server.rb"]:
            app_file = repo_root / pattern
            if app_file.exists():
                content = app_file.read_text()
                if "sinatra" in content or "require 'sinatra'" in content:
                    return True

        return False

    def _check_hanami_structure(self, repo_root: Path) -> bool:
        """Check if directory structure indicates Hanami project."""
        hanami_files = [
            repo_root / "Gemfile",
            repo_root / "config" / "app.rb",
            repo_root / "app",
        ]

        if all(f.exists() for f in hanami_files):
            gemfile = (repo_root / "Gemfile").read_text()
            if "hanami" in gemfile:
                return True

        return False

    def _extract_associations(self, source_code: str) -> list[str]:
        """Extract association definitions."""
        associations = []

        assoc_pattern = (
            r"(has_many|has_one|belongs_to|has_and_belongs_to_many)\s+:(\w+)"
        )

        for match in re.finditer(assoc_pattern, source_code):
            assoc_type = match.group(1)
            target = match.group(2)
            associations.append(f"{assoc_type} :{target}")

        return associations

    def _extract_validations(self, source_code: str) -> list[str]:
        """Extract validation definitions."""
        validations = []

        validation_pattern = r"validates\s+:(\w+)(?:\s*,\s*(.+?)(?=\n|validates))?"

        for match in re.finditer(validation_pattern, source_code, re.DOTALL):
            field = match.group(1)
            options = match.group(2) or ""
            validations.append(f"validates :{field} {options}".strip())

        return validations

    def _extract_callbacks(self, source_code: str) -> list[str]:
        """Extract callback definitions."""
        callbacks = []

        callback_types = [
            "before_save",
            "after_save",
            "before_create",
            "after_create",
            "before_destroy",
            "after_destroy",
            "before_update",
            "after_update",
        ]

        for callback_type in callback_types:
            callback_pattern = rf"{callback_type}\s+(?::(\w+)|->|{{\s*([^}}]+)\s*}})"

            for match in re.finditer(callback_pattern, source_code):
                method_name = match.group(1) or match.group(2) or ""
                callbacks.append(f"{callback_type} {method_name}".strip())

        return callbacks

    def _extract_scopes(self, source_code: str) -> list[str]:
        """Extract scope definitions."""
        scopes = []

        scope_pattern = r"scope\s+:(\w+)\s*,\s*->"

        for match in re.finditer(scope_pattern, source_code):
            scope_name = match.group(1)
            scopes.append(f"scope :{scope_name}")

        return scopes

    def get_framework_metadata(
        self, repo_root: Path | None = None, source_code: str | None = None
    ) -> dict[str, Any]:
        """Get all framework-specific metadata.

        Args:
            repo_root: Repository root path for project-level detection
            source_code: Ruby source code for file-level detection

        Returns:
            Dictionary with framework metadata

        Example:
            metadata = detector.get_framework_metadata(repo_root=Path("."))
            print(f"Framework: {metadata['framework_type']}")
        """
        framework = self.detect_from_project(repo_root)
        if not source_code and framework != RubyFrameworkType.NONE:
            source_code = ""
        elif source_code:
            file_framework = self.detect_from_source(source_code)
            if file_framework != RubyFrameworkType.NONE:
                framework = file_framework

        metadata = {
            "framework_type": framework.value,
            "detected": framework != RubyFrameworkType.NONE,
        }

        if framework == RubyFrameworkType.RAILS and repo_root:
            routes_file = Path(repo_root) / "config" / "routes.rb"
            if routes_file.exists():
                metadata["routes"] = self.extract_rails_routes(routes_file)

        if source_code and framework == RubyFrameworkType.RAILS:
            metadata["models"] = self.extract_rails_models(source_code)
            metadata["controllers"] = self.extract_rails_controllers(source_code)

        return metadata
