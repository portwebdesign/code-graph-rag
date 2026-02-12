import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PythonFrameworkType(Enum):
    """Supported Python frameworks."""

    DJANGO = "django"
    DJANGO_REST_FRAMEWORK = "django_rest_framework"
    FLASK = "flask"
    FASTAPI = "fastapi"
    SQLALCHEMY = "sqlalchemy"
    CELERY = "celery"
    PYTEST = "pytest"
    NONE = "none"


@dataclass
class FrameworkPattern:
    """Pattern definition for framework detection."""

    import_names: list[str] = field(default_factory=list)
    decorator_names: list[str] = field(default_factory=list)
    class_bases: list[str] = field(default_factory=list)
    method_patterns: list[str] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)


@dataclass
class DjangoEndpoint:
    """Django URL endpoint information."""

    path: str
    view_name: str
    http_methods: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)


@dataclass
class DjangoModel:
    """Django ORM model information."""

    name: str
    fields: dict[str, str] = field(default_factory=dict)
    relationships: list[str] = field(default_factory=list)
    meta_options: dict[str, Any] = field(default_factory=dict)


@dataclass
class FlaskRoute:
    """Flask route endpoint information."""

    path: str
    methods: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    handler_name: str = ""


@dataclass
class FastAPIRoute:
    """FastAPI endpoint information."""

    path: str
    method: str
    response_model: str | None = None
    dependencies: list[str] = field(default_factory=list)


@dataclass
class DRFViewSet:
    """Django REST Framework viewset information."""

    name: str
    base_class: str


@dataclass
class DRFSerializer:
    """Django REST Framework serializer information."""

    name: str
    base_class: str


class PythonFrameworkDetector:
    """Detect Python frameworks from AST nodes and source code.

    Patterns:
        - Django: imports, decorators, model classes, middleware
        - Flask: Flask imports, route decorators, blueprints
        - FastAPI: FastAPI imports, path operation decorators
        - SQLAlchemy: ORM session and model patterns
        - Celery: task decorators and celery app configuration
        - Pytest: test fixtures and assertions

    Example:
        detector = PythonFrameworkDetector()
        framework = detector.detect_framework(module_node, source_code)
        if framework == PythonFrameworkType.DJANGO:
            models = detector.extract_django_models(root_node, source_code)
    """

    FRAMEWORK_PATTERNS = {
        PythonFrameworkType.DJANGO: FrameworkPattern(
            import_names=["django", "from django"],
            decorator_names=[
                "@path",
                "@url",
                "@require_http_methods",
                "@login_required",
                "@permission_required",
                "@csrf_exempt",
            ],
            class_bases=[
                "models.Model",
                "views.View",
                "forms.Form",
                "ModelSerializer",
                "ViewSet",
                "APIView",
            ],
            method_patterns=["@csrf_exempt", "@login_required", "django.db.models"],
            config_files=["settings.py", "urls.py", "wsgi.py"],
        ),
        PythonFrameworkType.DJANGO_REST_FRAMEWORK: FrameworkPattern(
            import_names=["rest_framework", "from rest_framework"],
            decorator_names=["@api_view", "@action"],
            class_bases=[
                "APIView",
                "ViewSet",
                "ModelViewSet",
                "GenericViewSet",
                "Serializer",
                "ModelSerializer",
            ],
            method_patterns=["rest_framework", "serializer", "viewset"],
            config_files=["urls.py"],
        ),
        PythonFrameworkType.FLASK: FrameworkPattern(
            import_names=["flask", "from flask"],
            decorator_names=[
                "@app.route",
                "@app.before_request",
                "@app.after_request",
                "@Blueprint",
                "@route",
                "@before_request",
                "@after_request",
            ],
            class_bases=["Flask", "Blueprint", "Borg"],
            method_patterns=["@route", "@before_request", "flask.request"],
            config_files=["app.py", "application.py", "config.py"],
        ),
        PythonFrameworkType.FASTAPI: FrameworkPattern(
            import_names=["fastapi", "from fastapi"],
            decorator_names=[
                "@app.get",
                "@app.post",
                "@app.put",
                "@app.delete",
                "@app.patch",
                "@app.api_route",
                "@router.get",
                "@router.post",
                "@router.put",
                "@router.delete",
                "@router.patch",
                "@router.api_route",
                "@get",
                "@post",
                "@put",
                "@delete",
            ],
            class_bases=["BaseModel", "APIRouter", "FastAPI"],
            method_patterns=[
                "@get",
                "@post",
                "@patch",
                "@delete",
                "Pydantic",
                "Depends(",
            ],
            config_files=["main.py", "app.py"],
        ),
        PythonFrameworkType.SQLALCHEMY: FrameworkPattern(
            import_names=["sqlalchemy", "from sqlalchemy"],
            class_bases=["declarative_base", "Base", "Model"],
            method_patterns=["Column", "ForeignKey", "relationship"],
        ),
        PythonFrameworkType.CELERY: FrameworkPattern(
            import_names=["celery", "from celery"],
            decorator_names=["@app.task", "@task", "@celery.task"],
            class_bases=["Celery"],
            method_patterns=["@task", ".delay(", ".apply_async"],
        ),
        PythonFrameworkType.PYTEST: FrameworkPattern(
            import_names=["pytest", "from pytest"],
            decorator_names=["@pytest.fixture", "@fixture", "@parametrize"],
            method_patterns=["assert ", "pytest.raises", "conftest.py"],
        ),
    }

    def __init__(self, custom_patterns: dict | None = None):
        """Initialize detector with optional custom patterns.

        Args:
            custom_patterns: Optional dict mapping framework types to patterns
        """
        self.patterns = self.FRAMEWORK_PATTERNS.copy()
        if custom_patterns:
            self.patterns.update(custom_patterns)

    def detect_framework(
        self, module_node: Any, source_code: str
    ) -> PythonFrameworkType:
        """Detect framework used in Python module.

        Args:
            module_node: tree-sitter AST root node
            source_code: Complete source code string

        Returns:
            PythonFrameworkType detected framework, or NONE if not detected

        Example:
            framework = detector.detect_framework(tree_root, source)
            if framework == PythonFrameworkType.DJANGO:
                print("Django project detected")
        """
        for framework in [
            PythonFrameworkType.DJANGO_REST_FRAMEWORK,
            PythonFrameworkType.DJANGO,
            PythonFrameworkType.FASTAPI,
            PythonFrameworkType.FLASK,
            PythonFrameworkType.SQLALCHEMY,
            PythonFrameworkType.CELERY,
            PythonFrameworkType.PYTEST,
        ]:
            if self._matches_pattern(module_node, source_code, framework):
                return framework

        return PythonFrameworkType.NONE

    def _matches_pattern(
        self, module_node: Any, source: str, framework: PythonFrameworkType
    ) -> bool:
        """Check if source matches framework pattern.

        Args:
            module_node: AST root node
            source: Source code string
            framework: Framework type to check

        Returns:
            True if pattern matches
        """
        pattern = self.patterns[framework]

        for import_name in pattern.import_names:
            if import_name in source:
                return True

        decorators = self._extract_decorators(source)
        for dec in decorators:
            for pattern_dec in pattern.decorator_names:
                if pattern_dec.strip("@") in dec:
                    return True

        class_bases = self._extract_class_bases(source)
        for base in class_bases:
            for pattern_base in pattern.class_bases:
                if pattern_base in base:
                    return True

        return False

    def extract_django_endpoints(
        self, module_node: Any, source_code: str
    ) -> list[DjangoEndpoint]:
        """Extract Django URL endpoints.

        Args:
            module_node: AST root node
            source_code: Source code string

        Returns:
            List of DjangoEndpoint objects

        Example:
            endpoints = detector.extract_django_endpoints(root, source)
            for endpoint in endpoints:
                print(f"{endpoint.path} -> {endpoint.view_name}")
        """
        endpoints = []

        path_pattern = r'@path\(["\']([^"\']+)["\'],\s*(\w+(?:\.\w+)*)'
        for match in re.finditer(path_pattern, source_code):
            endpoints.append(
                DjangoEndpoint(
                    path=match.group(1),
                    view_name=match.group(2),
                    http_methods=["GET", "POST"],
                )
            )

        url_pattern = r'path\(["\']([^"\']+)["\'],\s*(\w+(?:\.\w+)*\.(?:view|views)\w+)'
        for match in re.finditer(url_pattern, source_code):
            endpoints.append(
                DjangoEndpoint(
                    path=match.group(1),
                    view_name=match.group(2),
                )
            )

        return endpoints

    def extract_django_models(
        self, module_node: Any, source_code: str
    ) -> list[DjangoModel]:
        """Extract Django ORM models.

        Args:
            module_node: AST root node
            source_code: Source code string

        Returns:
            List of DjangoModel objects

        Example:
            models = detector.extract_django_models(root, source)
            for model in models:
                print(f"Model: {model.name}")
                for field_name, field_type in model.fields.items():
                    print(f"  {field_name}: {field_type}")
        """
        models = []

        class_pattern = r"class\s+(\w+)\([^)]*models\.Model[^)]*\):"
        for match in re.finditer(class_pattern, source_code):
            model_name = match.group(1)

            fields = self._extract_model_fields(source_code, model_name)
            relationships = self._extract_model_relationships(source_code, model_name)

            models.append(
                DjangoModel(
                    name=model_name,
                    fields=fields,
                    relationships=relationships,
                )
            )

        return models

    def extract_flask_routes(
        self, module_node: Any, source_code: str
    ) -> list[FlaskRoute]:
        """Extract Flask route definitions.

        Args:
            module_node: AST root node
            source_code: Source code string

        Returns:
            List of FlaskRoute objects

        Example:
            routes = detector.extract_flask_routes(root, source)
            for route in routes:
                print(f"{route.path} ({', '.join(route.methods)})")
        """
        routes = []

        route_pattern = (
            r'@app\.route\(["\']([^"\']+)["\'](?:,\s*methods=\[([^\]]+)\])?\)'
        )
        for match in re.finditer(route_pattern, source_code):
            path = match.group(1)
            methods_str = match.group(2) or "GET"

            methods = [m.strip().strip("'\"") for m in methods_str.split(",")]

            routes.append(
                FlaskRoute(
                    path=path,
                    methods=methods,
                )
            )

        bp_route_pattern = r'@(\w+)\.route\(["\']([^"\']+)["\']'
        for match in re.finditer(bp_route_pattern, source_code):
            bp_name = match.group(1)
            path = match.group(2)

            routes.append(
                FlaskRoute(
                    path=path,
                    methods=["GET"],
                    decorators=[f"@{bp_name}.route"],
                )
            )

        return routes

    def extract_fastapi_routes(
        self, module_node: Any, source_code: str
    ) -> list[FastAPIRoute]:
        """Extract FastAPI route definitions.

        Args:
            module_node: AST root node
            source_code: Source code string

        Returns:
            List of FastAPIRoute objects

        Example:
            routes = detector.extract_fastapi_routes(root, source)
            for route in routes:
                print(f"{route.method.upper()} {route.path}")
        """
        routes = []

        pattern = r"@(?P<router>\w+)\.(?P<method>get|post|put|delete|patch|api_route)\((?P<args>[^)]*)\)"
        for match in re.finditer(pattern, source_code):
            method = match.group("method")
            args = match.group("args")
            path = self._extract_first_string_arg(args)
            if not path:
                continue
            response_model = self._extract_response_model(args)
            dependencies = self._extract_dependencies(args)
            if method == "api_route":
                api_method = self._extract_api_route_method(args)
                routes.append(
                    FastAPIRoute(
                        path=path,
                        method=api_method,
                        response_model=response_model,
                        dependencies=dependencies,
                    )
                )
                continue
            routes.append(
                FastAPIRoute(
                    path=path,
                    method=method.upper(),
                    response_model=response_model,
                    dependencies=dependencies,
                )
            )

        return routes

    def _extract_first_string_arg(self, args: str) -> str | None:
        match = re.search(r'["\"]([^"\"]+)["\"]', args)
        if not match:
            return None
        return match.group(1)

    def _extract_response_model(self, args: str) -> str | None:
        match = re.search(r"response_model\s*=\s*([A-Za-z_][\w\.]*)", args)
        if not match:
            return None
        return match.group(1)

    def _extract_dependencies(self, args: str) -> list[str]:
        dependencies = []
        for match in re.finditer(r"Depends\(\s*([A-Za-z_][\w\.]*)?\s*\)", args):
            dep = match.group(1)
            if dep:
                dependencies.append(dep)
        return dependencies

    def _extract_api_route_method(self, args: str) -> str:
        match = re.search(r"methods\s*=\s*\[([^\]]+)\]", args)
        if not match:
            return "ANY"
        methods_raw = match.group(1)
        methods = [m.strip().strip("'\"") for m in methods_raw.split(",") if m.strip()]
        if not methods:
            return "ANY"
        return methods[0].upper()

    def _extract_decorators(self, source_code: str) -> list[str]:
        """Extract all decorators from source code."""
        decorator_pattern = r"@(\w+(?:\.\w+)*(?:\([^)]*\))?)"
        return re.findall(decorator_pattern, source_code)

    def _extract_class_bases(self, source_code: str) -> list[str]:
        """Extract class base classes from source code."""
        class_pattern = r"class\s+\w+\(([^)]+)\):"
        bases = []
        for match in re.finditer(class_pattern, source_code):
            base_str = match.group(1)
            bases.extend([b.strip() for b in base_str.split(",")])
        return bases

    def _extract_model_fields(
        self, source_code: str, model_name: str
    ) -> dict[str, str]:
        """Extract model fields from Django model class."""
        fields = {}

        class_pattern = rf"class\s+{model_name}\([^)]*\):\s*\n(.*?)(?=\nclass|\Z)"
        match = re.search(class_pattern, source_code, re.DOTALL)
        if match:
            class_body = match.group(1)

            field_pattern = r"(\w+)\s*=\s*models\.(\w+Field)"
            for field_match in re.finditer(field_pattern, class_body):
                field_name = field_match.group(1)
                field_type = field_match.group(2)
                fields[field_name] = field_type

        return fields

    def _extract_model_relationships(
        self, source_code: str, model_name: str
    ) -> list[str]:
        """Extract relationships from Django model."""
        relationships = []

        class_pattern = rf"class\s+{model_name}\([^)]*\):\s*\n(.*?)(?=\nclass|\Z)"
        match = re.search(class_pattern, source_code, re.DOTALL)
        if match:
            class_body = match.group(1)

            rel_pattern = r"(\w+)\s*=\s*models\.(ForeignKey|ManyToMany)\(([^)]+)\)"
            for rel_match in re.finditer(rel_pattern, class_body):
                field_name = rel_match.group(1)
                rel_type = rel_match.group(2)
                rel_model = rel_match.group(3).strip().strip("'\"")
                relationships.append(f"{field_name}: {rel_type}({rel_model})")

        return relationships

    def _extract_drf_viewsets(self, source_code: str) -> list[DRFViewSet]:
        """Extract DRF viewsets from source code."""
        viewsets = []
        class_pattern = r"class\s+(\w+)\(([^)]+)\):"
        for match in re.finditer(class_pattern, source_code):
            name = match.group(1)
            bases = match.group(2)
            if "ViewSet" in bases or "APIView" in bases:
                viewsets.append(DRFViewSet(name=name, base_class=bases))
        return viewsets

    def _extract_drf_serializers(self, source_code: str) -> list[DRFSerializer]:
        """Extract DRF serializers from source code."""
        serializers = []
        class_pattern = r"class\s+(\w+)\(([^)]+)\):"
        for match in re.finditer(class_pattern, source_code):
            name = match.group(1)
            bases = match.group(2)
            if "Serializer" in bases:
                serializers.append(DRFSerializer(name=name, base_class=bases))
        return serializers

    def get_framework_metadata(
        self, framework: PythonFrameworkType, module_node: Any, source_code: str
    ) -> dict[str, Any]:
        """Get all framework-specific metadata.

        Args:
            framework: Framework type
            module_node: AST root node
            source_code: Source code string

        Returns:
            Dictionary with framework-specific metadata

        Example:
            metadata = detector.get_framework_metadata(framework, root, source)
            print(f"Framework: {metadata['framework_type']}")
            print(f"Endpoints: {len(metadata['endpoints'])}")
        """
        metadata = {
            "framework_type": framework.value,
            "detected": framework != PythonFrameworkType.NONE,
        }

        if framework == PythonFrameworkType.DJANGO:
            metadata["endpoints"] = self.extract_django_endpoints(
                module_node, source_code
            )
            metadata["models"] = self.extract_django_models(module_node, source_code)
        elif framework == PythonFrameworkType.DJANGO_REST_FRAMEWORK:
            metadata["viewsets"] = self._extract_drf_viewsets(source_code)
            metadata["serializers"] = self._extract_drf_serializers(source_code)
        elif framework == PythonFrameworkType.FLASK:
            metadata["routes"] = self.extract_flask_routes(module_node, source_code)
        elif framework == PythonFrameworkType.FASTAPI:
            metadata["routes"] = self.extract_fastapi_routes(module_node, source_code)

        return metadata
