import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class RubyVisibility(Enum):
    """Ruby method visibility."""

    PUBLIC = "public"
    PRIVATE = "private"
    PROTECTED = "protected"


@dataclass
class RubyMethod:
    """Ruby method representation."""

    name: str
    parameters: list[str] = field(default_factory=list)
    visibility: RubyVisibility = RubyVisibility.PUBLIC
    is_block: bool = False
    is_lambda: bool = False
    line_number: int | None = None
    docstring: str | None = None


@dataclass
class RubyClass:
    """Ruby class representation."""

    name: str
    superclass: str | None = None
    modules_mixed: list[str] = field(default_factory=list)
    line_number: int | None = None


@dataclass
class RubyModule:
    """Ruby module representation."""

    name: str
    line_number: int | None = None


@dataclass
class RailsAssociation:
    """Rails Active Record association."""

    type: str
    target: str
    options: dict[str, str] = field(default_factory=dict)


@dataclass
class RailsValidation:
    """Rails Active Record validation."""

    attribute: str
    validators: list[str] = field(default_factory=list)
    options: dict[str, str] = field(default_factory=dict)


@dataclass
class RailsScope:
    """Rails Active Record scope."""

    name: str
    parameters: list[str] = field(default_factory=list)
    line_number: int | None = None


@dataclass
class RailsCallback:
    """Rails Active Record callback."""

    event: str
    actions: list[str] = field(default_factory=list)
    conditions: dict[str, str] = field(default_factory=dict)


@dataclass
class RubyFileDefinitions:
    """All definitions extracted from a Ruby file."""

    methods: list[RubyMethod] = field(default_factory=list)
    classes: list[RubyClass] = field(default_factory=list)
    modules: list[RubyModule] = field(default_factory=list)
    constants: list[str] = field(default_factory=list)
    gems_required: list[str] = field(default_factory=list)


@dataclass
class RailsModelInfo:
    """Rails model-specific information."""

    class_name: str
    associations: list[RailsAssociation] = field(default_factory=list)
    validations: list[RailsValidation] = field(default_factory=list)
    scopes: list[RailsScope] = field(default_factory=list)
    callbacks: list[RailsCallback] = field(default_factory=list)
    timestamps: bool = False
    table_name: str | None = None


class RubyParserMixin:
    """Ruby-specific parsing logic for tree-sitter integration."""

    def extract_ruby_definitions(
        self, content: str, file_path: str | None = None
    ) -> RubyFileDefinitions:
        """
        Extract all definitions from Ruby source code.

        Args:
            content: Ruby source code
            file_path: Optional path to file

        Returns:
            RubyFileDefinitions with methods, classes, modules, constants
        """
        definitions = RubyFileDefinitions()

        definitions.gems_required = self._extract_requires(content)

        definitions.classes = self._extract_classes(content)

        definitions.modules = self._extract_modules(content)

        definitions.methods = self._extract_methods(content)

        definitions.constants = self._extract_constants(content)

        return definitions

    def _extract_requires(self, content: str) -> list[str]:
        """Extract require and gem statements."""
        requires = []

        require_pattern = r"^\s*require\s+['\"]([^'\"]+)['\"]"
        for match in re.finditer(require_pattern, content, re.MULTILINE):
            requires.append(match.group(1))

        gem_pattern = r"^\s*gem\s+['\"]([^'\"]+)['\"]"
        for match in re.finditer(gem_pattern, content, re.MULTILINE):
            requires.append(match.group(1))

        return requires

    def _extract_classes(self, content: str) -> list[RubyClass]:
        """Extract class definitions."""
        classes = []

        class_pattern = r"^\s*class\s+([A-Z]\w*)\s*(?:<\s*([A-Z]\w*))?"
        line_num = 0

        for line_num, line in enumerate(content.split("\n"), 1):
            match = re.match(class_pattern, line)
            if match:
                class_name = match.group(1)
                superclass = match.group(2)
                classes.append(
                    RubyClass(
                        name=class_name, superclass=superclass, line_number=line_num
                    )
                )

        return classes

    def _extract_modules(self, content: str) -> list[RubyModule]:
        """Extract module definitions."""
        modules = []

        module_pattern = r"^\s*module\s+([A-Z]\w*)"

        for line_num, line in enumerate(content.split("\n"), 1):
            match = re.match(module_pattern, line)
            if match:
                modules.append(RubyModule(name=match.group(1), line_number=line_num))

        return modules

    def _extract_methods(self, content: str) -> list[RubyMethod]:
        """Extract method definitions."""
        methods = []

        method_pattern = r"^\s*def\s+([a-z_][a-z0-9_]*[!?]?)\s*\(([^)]*)\)"

        current_visibility = RubyVisibility.PUBLIC

        for line_num, line in enumerate(content.split("\n"), 1):
            if re.match(r"^\s*(private|protected|public)\s*$", line):
                visibility_match = re.match(r"^\s*(\w+)", line)
                if visibility_match:
                    visibility_str = visibility_match.group(1)
                    current_visibility = RubyVisibility(visibility_str)
                continue

            match = re.match(method_pattern, line)
            if match:
                method_name = match.group(1)
                params_str = match.group(2).strip()
                parameters = [p.strip() for p in params_str.split(",") if p.strip()]

                methods.append(
                    RubyMethod(
                        name=method_name,
                        parameters=parameters,
                        visibility=current_visibility,
                        line_number=line_num,
                    )
                )

        return methods

    def _extract_constants(self, content: str) -> list[str]:
        """Extract constant assignments."""
        constants = []

        constant_pattern = r"^\s*([A-Z][A-Z0-9_]*)\s*="

        for line in content.split("\n"):
            match = re.match(constant_pattern, line)
            if match:
                constants.append(match.group(1))

        return constants

    def extract_rails_models(self, content: str, class_name: str) -> RailsModelInfo:
        """
        Extract Rails Active Record model information.

        Args:
            content: Ruby class content
            class_name: Name of the model class

        Returns:
            RailsModelInfo with associations, validations, scopes, callbacks
        """
        model = RailsModelInfo(class_name=class_name)

        model.associations = self._extract_associations(content)

        model.validations = self._extract_validations(content)

        model.scopes = self._extract_scopes(content)

        model.callbacks = self._extract_callbacks(content)

        model.timestamps = bool(re.search(r"timestamps", content))

        table_name_match = re.search(
            r"self\.table_name\s*=\s*['\"]([^'\"]+)['\"]", content
        )
        if table_name_match:
            model.table_name = table_name_match.group(1)

        return model

    def _extract_associations(self, content: str) -> list[RailsAssociation]:
        """Extract Rails associations (has_many, belongs_to, etc.)."""
        associations = []

        patterns = {
            "has_one": r"has_one\s+:([a-z_]+)",
            "has_many": r"has_many\s+:([a-z_]+)",
            "belongs_to": r"belongs_to\s+:([a-z_]+)",
            "has_and_belongs_to_many": r"has_and_belongs_to_many\s+:([a-z_]+)",
            "polymorphic": r"as:\s*:([a-z_]+)",
        }

        for assoc_type, pattern in patterns.items():
            for match in re.finditer(pattern, content):
                associations.append(
                    RailsAssociation(type=assoc_type, target=match.group(1))
                )

        return associations

    def _extract_validations(self, content: str) -> list[RailsValidation]:
        """Extract Rails validations."""
        validations = []

        validation_pattern = r"validates\s+:([a-z_]+),\s*(.+?)(?=\n|validates)"

        for match in re.finditer(validation_pattern, content, re.DOTALL):
            attribute = match.group(1)
            validators_str = match.group(2)

            validators = []
            validator_pattern = r"(\w+):\s*(?:true|{[^}]*}|[^,]+)"
            for val_match in re.finditer(validator_pattern, validators_str):
                validators.append(val_match.group(1))

            validations.append(
                RailsValidation(attribute=attribute, validators=validators)
            )

        return validations

    def _extract_scopes(self, content: str) -> list[RailsScope]:
        """Extract Rails scopes."""
        scopes = []

        scope_pattern = r"scope\s+:([a-z_]+)"

        for line_num, line in enumerate(content.split("\n"), 1):
            match = re.search(scope_pattern, line)
            if match:
                scope_name = match.group(1)
                scopes.append(RailsScope(name=scope_name, line_number=line_num))

        return scopes

    def _extract_callbacks(self, content: str) -> list[RailsCallback]:
        """Extract Rails callbacks (before_save, after_create, etc.)."""
        callbacks = []

        callback_patterns = [
            "before_save",
            "after_save",
            "before_create",
            "after_create",
            "before_update",
            "after_update",
            "before_destroy",
            "after_destroy",
            "before_validation",
            "after_validation",
        ]

        for callback_type in callback_patterns:
            pattern = f"{callback_type}\\s+:([a-z_]+)"
            for match in re.finditer(pattern, content):
                callbacks.append(
                    RailsCallback(event=callback_type, actions=[match.group(1)])
                )

        return callbacks

    def extract_rails_routes(self, content: str) -> list[dict[str, str]]:
        """
        Extract routes from Rails routes.rb file.

        Args:
            content: routes.rb content

        Returns:
            List of route dictionaries with method, path, controller, action
        """
        routes = []

        route_pattern = r"(get|post|put|patch|delete|resources)\s+['\"]([^'\"]+)['\"]"

        for match in re.finditer(route_pattern, content):
            method = match.group(1)
            path = match.group(2)

            routes.append(
                {
                    "method": method.upper(),
                    "path": path,
                }
            )

        resource_pattern = r"resources\s+:([a-z_]+)"
        for match in re.finditer(resource_pattern, content):
            resource = match.group(1)
            routes.append(
                {
                    "method": "RESOURCE",
                    "path": f"/{resource}",
                }
            )

        return routes

    def analyze_ruby_file(self, file_path: str) -> dict:
        """
        Complete analysis of a Ruby file.

        Args:
            file_path: Path to Ruby file

        Returns:
            Dictionary with comprehensive analysis
        """
        path = Path(file_path)
        content = path.read_text(encoding="utf-8", errors="ignore")

        analysis = {
            "file_path": str(file_path),
            "definitions": self.extract_ruby_definitions(content, file_path),
            "is_rails_model": self._is_rails_model(content),
            "is_routes_file": self._is_routes_file(file_path, content),
        }

        if analysis["is_rails_model"]:
            definitions = analysis.get("definitions")
            if isinstance(definitions, RubyFileDefinitions):
                classes = definitions.classes
                if classes:
                    class_name = classes[0].name
                    analysis["rails_model"] = self.extract_rails_models(
                        content, class_name
                    )

        if analysis["is_routes_file"]:
            analysis["routes"] = self.extract_rails_routes(content)

        return analysis

    def _is_rails_model(self, content: str) -> bool:
        """Check if file is a Rails model (inherits from ApplicationRecord)."""
        return bool(re.search(r"class\s+\w+\s*<\s*ApplicationRecord", content))

    def _is_routes_file(self, file_path: str, content: str) -> bool:
        """Check if file is Rails routes.rb."""
        return "routes.rb" in file_path or "Rails.application.routes.draw" in content
