import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class JavaFrameworkType(Enum):
    """Supported Java frameworks."""

    SPRING_BOOT = "spring_boot"
    SPRING_MVC = "spring_mvc"
    JAKARTA = "jakarta"
    QUARKUS = "quarkus"
    MICRONAUT = "micronaut"
    GRPC = "grpc"
    NONE = "none"


@dataclass
class JavaAnnotation:
    """Java annotation information."""

    name: str
    parameters: dict[str, Any] = field(default_factory=dict)
    full_name: str | None = None


@dataclass
class SpringEndpoint:
    """Spring REST endpoint information."""

    path: str
    method: str
    controller_class: str
    handler_method: str
    parameters: list[str] = field(default_factory=list)
    produces: str | None = None
    consumes: str | None = None


@dataclass
class SpringEntity:
    """Spring JPA entity information."""

    class_name: str
    table_name: str | None = None
    fields: dict[str, str] = field(default_factory=dict)
    relationships: list[str] = field(default_factory=list)


@dataclass
class SpringRepository:
    """Spring Data repository information."""

    class_name: str
    entity_type: str
    id_type: str
    custom_methods: list[str] = field(default_factory=list)


class JavaFrameworkDetector:
    """Detect Java frameworks from source code and project structure.

    Detection:
        - Spring Boot: @SpringBootApplication
        - Spring MVC: @Controller, @RestController
        - Jakarta: @WebServlet, @ApplicationPath
        - Quarkus: quarkus imports and annotations
        - Micronaut: @Micronaut, @Controller annotations

    Example:
        detector = JavaFrameworkDetector()
        framework = detector.detect_framework(source_code)
        if framework == JavaFrameworkType.SPRING_BOOT:
            endpoints = detector.extract_endpoints(source_code)
    """

    SPRING_ANNOTATIONS = {
        "boot": ["@SpringBootApplication"],
        "controllers": ["@RestController", "@Controller"],
        "services": ["@Service", "@Component"],
        "repositories": ["@Repository"],
        "mapping": [
            "@GetMapping",
            "@PostMapping",
            "@PutMapping",
            "@DeleteMapping",
            "@PatchMapping",
        ],
        "request_mapping": ["@RequestMapping"],
        "entities": ["@Entity", "@Table"],
        "dto": ["@Data", "@Getter", "@Setter"],
        "config": ["@Configuration", "@EnableWebMvc"],
    }

    JAKARTA_ANNOTATIONS = {
        "servlet": ["@WebServlet"],
        "application": ["@ApplicationPath"],
        "rest": ["@Path", "@GET", "@POST", "@PUT", "@DELETE"],
        "persistence": ["@Entity", "@Table", "@Column"],
    }

    QUARKUS_INDICATORS = {
        "imports": ["io.quarkus", "com.quarkus"],
        "annotations": ["@QuarkusTest", "@QuarkusMain"],
    }

    MICRONAUT_ANNOTATIONS = {
        "application": ["@Micronaut"],
        "controller": ["@Controller"],
        "http": ["@Get", "@Post", "@Put", "@Delete"],
        "injectable": ["@Singleton", "@Prototype"],
    }

    def __init__(self):
        """Initialize Java framework detector."""
        pass

    def detect_framework(self, source_code: str) -> JavaFrameworkType:
        """Detect Java framework from source code.

        Args:
            source_code: Java source code as string

        Returns:
            JavaFrameworkType detected framework

        Example:
            framework = detector.detect_framework(java_source)
            print(f"Framework: {framework.value}")
        """
        if "@SpringBootApplication" in source_code:
            return JavaFrameworkType.SPRING_BOOT

        spring_mvc_indicators = [
            "@RestController",
            "@Controller",
            "@Service",
            "import org.springframework",
            "import spring",
        ]
        if any(indicator in source_code for indicator in spring_mvc_indicators):
            return JavaFrameworkType.SPRING_MVC

        jakarta_indicators = [
            "@WebServlet",
            "@ApplicationPath",
            "@Path",
            "import jakarta.ws.rs",
            "import javax.ws.rs",
        ]
        if any(indicator in source_code for indicator in jakarta_indicators):
            return JavaFrameworkType.JAKARTA

        if any(
            indicator in source_code for indicator in self.QUARKUS_INDICATORS["imports"]
        ):
            return JavaFrameworkType.QUARKUS

        micronaut_indicators = ["import io.micronaut", "@Micronaut", "@Controller"]
        if any(indicator in source_code for indicator in micronaut_indicators):
            return JavaFrameworkType.MICRONAUT

        grpc_indicators = ["io.grpc", "GrpcService", "BindableService"]
        if any(indicator in source_code for indicator in grpc_indicators):
            return JavaFrameworkType.GRPC

        return JavaFrameworkType.NONE

    def extract_annotations(
        self, source_code: str, class_name: str | None = None
    ) -> list[JavaAnnotation]:
        """Extract all annotations from source code.

        Args:
            source_code: Java source code
            class_name: Optional class name to extract annotations for that class only

        Returns:
            List of JavaAnnotation objects

        Example:
            annotations = detector.extract_annotations(source_code, "UserController")
            for ann in annotations:
                print(f"@{ann.name}")
        """
        annotations = []

        pattern = r"@(\w+)(?:\(([^)]*)\))?"

        for match in re.finditer(pattern, source_code):
            name = match.group(1)
            params_str = match.group(2)

            params = {}
            if params_str:
                param_pattern = r'(\w+)\s*=\s*["\']?([^"\',]+)["\']?'
                for param_match in re.finditer(param_pattern, params_str):
                    params[param_match.group(1)] = param_match.group(2)

            annotations.append(
                JavaAnnotation(
                    name=name,
                    parameters=params,
                )
            )

        return annotations

    def extract_endpoints(self, source_code: str) -> list[SpringEndpoint]:
        """Extract REST endpoints from Spring controller.

        Args:
            source_code: Java source code

        Returns:
            List of SpringEndpoint objects

        Example:
            endpoints = detector.extract_endpoints(source_code)
            for endpoint in endpoints:
                print(f"{endpoint.method} {endpoint.path}")
        """
        endpoints = []

        class_pattern = r"(?:public\s+)?class\s+(\w+)"
        class_match = re.search(class_pattern, source_code)
        controller_class = class_match.group(1) if class_match else "UnknownController"

        class_mapping_pattern = r'@RequestMapping\(["\']([^"\']+)["\']'
        class_path = ""
        class_mapping = re.search(class_mapping_pattern, source_code)
        if class_mapping:
            class_path = class_mapping.group(1)

        mapping_pattern = r"@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping)\(([^)]*)\)\s*(?:public|private|protected)?\s*\w+[\s\w<>,]*\s+(\w+)\s*\("

        for match in re.finditer(mapping_pattern, source_code):
            method_type = match.group(1).replace("Mapping", "").upper()
            path_str = match.group(2)
            handler_method = match.group(3)

            path_value = ""
            path_match = re.search(
                r'value\s*=\s*["\']([^"\']+)["\']|["\']([^"\']+)["\']', path_str
            )
            if path_match:
                path_value = path_match.group(1) or path_match.group(2)

            full_path = class_path + (path_value or "")

            endpoints.append(
                SpringEndpoint(
                    path=full_path or "/",
                    method=method_type,
                    controller_class=controller_class,
                    handler_method=handler_method,
                )
            )

        request_mapping_pattern = r"@RequestMapping\(([^)]*)\)\s*(?:public|private|protected)?\s*\w+[\s\w<>,]*\s+(\w+)\s*\("
        for match in re.finditer(request_mapping_pattern, source_code):
            mapping_str = match.group(1)
            handler_method = match.group(2)

            path_match = re.search(
                r'value\s*=\s*["\']([^"\']+)["\']|["\']([^"\']+)["\']', mapping_str
            )
            path_value = (
                path_match.group(1) or path_match.group(2) if path_match else "/"
            )

            method_match = re.search(r"method\s*=\s*RequestMethod\.(\w+)", mapping_str)
            method = method_match.group(1) if method_match else "GET"

            endpoints.append(
                SpringEndpoint(
                    path=class_path + path_value,
                    method=method,
                    controller_class=controller_class,
                    handler_method=handler_method,
                )
            )

        return endpoints

    def extract_entities(self, source_code: str) -> list[SpringEntity]:
        """Extract JPA entities from source code.

        Args:
            source_code: Java source code

        Returns:
            List of SpringEntity objects

        Example:
            entities = detector.extract_entities(source_code)
            for entity in entities:
                print(f"Entity: {entity.class_name}")
                for field_name, field_type in entity.fields.items():
                    print(f"  {field_name}: {field_type}")
        """
        entities = []

        if "@Entity" not in source_code:
            return entities

        class_pattern = r"@Entity\s*(?:@Table\([^)]*\))?\s*(?:public\s+)?class\s+(\w+)"
        class_match = re.search(class_pattern, source_code)
        if not class_match:
            return entities

        class_name = class_match.group(1)

        table_name = None
        table_pattern = r'@Table\(name\s*=\s*["\']([^"\']+)["\']'
        table_match = re.search(table_pattern, source_code)
        if table_match:
            table_name = table_match.group(1)

        fields = self._extract_entity_fields(source_code)
        relationships = self._extract_entity_relationships(source_code)

        entities.append(
            SpringEntity(
                class_name=class_name,
                table_name=table_name,
                fields=fields,
                relationships=relationships,
            )
        )

        return entities

    def extract_repositories(self, source_code: str) -> list[SpringRepository]:
        """Extract Spring Data repositories.

        Args:
            source_code: Java source code

        Returns:
            List of SpringRepository objects

        Example:
            repos = detector.extract_repositories(source_code)
            for repo in repos:
                print(f"Repository: {repo.class_name}")
                print(f"Entity: {repo.entity_type}")
        """
        repositories = []

        if "@Repository" not in source_code and "extends Repository" not in source_code:
            return repositories

        interface_pattern = r"(?:public\s+)?interface\s+(\w+)\s+extends\s+(\w+)\s*<\s*(\w+)\s*,\s*(\w+)\s*>"
        interface_match = re.search(interface_pattern, source_code)

        if interface_match:
            repo_name = interface_match.group(1)
            entity_type = interface_match.group(3)
            id_type = interface_match.group(4)

            custom_methods = self._extract_custom_methods(source_code)

            repositories.append(
                SpringRepository(
                    class_name=repo_name,
                    entity_type=entity_type,
                    id_type=id_type,
                    custom_methods=custom_methods,
                )
            )

        return repositories

    def _extract_entity_fields(self, source_code: str) -> dict[str, str]:
        """Extract entity field definitions."""
        fields = {}

        field_pattern = r"(?:@Column\([^)]*\))?\s*(?:private|protected|public)\s+(\w+)\s+(\w+)(?:\s*=|;)"

        for match in re.finditer(field_pattern, source_code):
            field_type = match.group(1)
            field_name = match.group(2)

            if field_type in ["@Column", "@Id", "@GeneratedValue"]:
                continue

            fields[field_name] = field_type

        return fields

    def _extract_entity_relationships(self, source_code: str) -> list[str]:
        """Extract relationship annotations."""
        relationships = []

        rel_pattern = r"@(OneToMany|ManyToOne|ManyToMany|OneToOne)\([^)]*\)\s*(?:private|protected|public)\s+(?:\w+<)?\s*(\w+)"

        for match in re.finditer(rel_pattern, source_code):
            rel_type = match.group(1)
            target_type = match.group(2)
            relationships.append(f"{rel_type}({target_type})")

        return relationships

    def _extract_custom_methods(self, source_code: str) -> list[str]:
        """Extract custom query method signatures."""
        methods = []

        method_pattern = r"(?:public)?\s+(?:Optional<)?\w+(?:>)?\s+(\w+)\s*\([^)]*\);"

        base_methods = {"save", "delete", "deleteById", "findById", "findAll"}

        for match in re.finditer(method_pattern, source_code):
            method_name = match.group(1)
            if method_name not in base_methods:
                methods.append(method_name)

        return methods

    def get_framework_metadata(self, source_code: str) -> dict[str, Any]:
        """Get all framework-specific metadata.

        Args:
            source_code: Java source code

        Returns:
            Dictionary with framework metadata

        Example:
            metadata = detector.get_framework_metadata(source_code)
            print(f"Framework: {metadata['framework_type']}")
            if metadata['endpoints']:
                for ep in metadata['endpoints']:
                    print(f"  {ep.method} {ep.path}")
        """
        framework = self.detect_framework(source_code)

        metadata = {
            "framework_type": framework.value,
            "detected": framework != JavaFrameworkType.NONE,
            "annotations": self.extract_annotations(source_code),
        }

        if framework in [JavaFrameworkType.SPRING_BOOT, JavaFrameworkType.SPRING_MVC]:
            metadata["endpoints"] = self.extract_endpoints(source_code)
            metadata["entities"] = self.extract_entities(source_code)
            metadata["repositories"] = self.extract_repositories(source_code)
        elif framework == JavaFrameworkType.GRPC:
            metadata["grpc_services"] = self.extract_grpc_services(source_code)

        return metadata

    def extract_grpc_services(self, source_code: str) -> list[str]:
        services: list[str] = []
        class_pattern = r"class\s+(\w+)\s+extends\s+\w+ImplBase"
        for match in re.finditer(class_pattern, source_code):
            services.append(match.group(1))
        return services
