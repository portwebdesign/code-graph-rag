import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class KotlinModifier(Enum):
    """Kotlin modifiers."""

    OPEN = "open"
    FINAL = "final"
    ABSTRACT = "abstract"
    SEALED = "sealed"
    DATA = "data"
    ENUM = "enum"
    INNER = "inner"
    COMPANION = "companion"
    INLINE = "inline"
    INFIX = "infix"


class KotlinVisibility(Enum):
    """Kotlin visibility modifiers."""

    PUBLIC = "public"
    PRIVATE = "private"
    PROTECTED = "protected"
    INTERNAL = "internal"


@dataclass
class KotlinParameter:
    """Kotlin function parameter."""

    name: str
    type: str | None = None
    default_value: str | None = None
    is_vararg: bool = False


@dataclass
class KotlinFunction:
    """Kotlin function representation."""

    name: str
    parameters: list[KotlinParameter] = field(default_factory=list)
    return_type: str | None = None
    visibility: KotlinVisibility = KotlinVisibility.PUBLIC
    modifiers: list[KotlinModifier] = field(default_factory=list)
    is_extension: bool = False
    receiver_type: str | None = None
    is_suspend: bool = False
    is_infix: bool = False
    line_number: int | None = None
    docstring: str | None = None


@dataclass
class KotlinProperty:
    """Kotlin property (var/val)."""

    name: str
    type: str | None = None
    is_mutable: bool = False
    initial_value: str | None = None
    visibility: KotlinVisibility = KotlinVisibility.PUBLIC
    line_number: int | None = None


@dataclass
class KotlinClass:
    """Kotlin class representation."""

    name: str
    modifiers: list[KotlinModifier] = field(default_factory=list)
    superclass: str | None = None
    interfaces: list[str] = field(default_factory=list)
    properties: list[KotlinProperty] = field(default_factory=list)
    functions: list[KotlinFunction] = field(default_factory=list)
    type_parameters: list[str] = field(default_factory=list)
    visibility: KotlinVisibility = KotlinVisibility.PUBLIC
    line_number: int | None = None
    docstring: str | None = None


@dataclass
class KotlinInterface:
    """Kotlin interface representation."""

    name: str
    functions: list[KotlinFunction] = field(default_factory=list)
    properties: list[KotlinProperty] = field(default_factory=list)
    type_parameters: list[str] = field(default_factory=list)
    superinterfaces: list[str] = field(default_factory=list)
    line_number: int | None = None
    docstring: str | None = None


@dataclass
class KotlinExtensionFunction:
    """Kotlin extension function."""

    name: str
    receiver_type: str
    parameters: list[KotlinParameter] = field(default_factory=list)
    return_type: str | None = None
    modifiers: list[KotlinModifier] = field(default_factory=list)
    line_number: int | None = None


@dataclass
class KotlinFileDefinitions:
    """All definitions extracted from a Kotlin file."""

    package: str | None = None
    imports: list[str] = field(default_factory=list)
    classes: list[KotlinClass] = field(default_factory=list)
    interfaces: list[KotlinInterface] = field(default_factory=list)
    functions: list[KotlinFunction] = field(default_factory=list)
    extension_functions: list[KotlinExtensionFunction] = field(default_factory=list)
    enums: list[str] = field(default_factory=list)
    sealed_classes: list[str] = field(default_factory=list)
    data_classes: list[str] = field(default_factory=list)
    type_aliases: dict[str, str] = field(default_factory=dict)


class KotlinParserMixin:
    """Kotlin-specific parsing logic for tree-sitter integration."""

    def extract_kotlin_definitions(
        self, content: str, file_path: str | None = None
    ) -> KotlinFileDefinitions:
        """
        Extract all definitions from Kotlin source code.

        Args:
            content: Kotlin source code
            file_path: Optional path to file

        Returns:
            KotlinFileDefinitions with classes, functions, interfaces, etc.
        """
        definitions = KotlinFileDefinitions()

        definitions.package = self._extract_package(content)
        definitions.imports = self._extract_imports(content)

        definitions.classes = self._extract_classes(content)

        definitions.interfaces = self._extract_interfaces(content)

        definitions.functions = self._extract_functions(content)

        definitions.extension_functions = self._extract_extension_functions(content)

        definitions.enums = self._extract_enums(content)

        definitions.sealed_classes = [
            c.name for c in definitions.classes if KotlinModifier.SEALED in c.modifiers
        ]

        definitions.data_classes = [
            c.name for c in definitions.classes if KotlinModifier.DATA in c.modifiers
        ]

        definitions.type_aliases = self._extract_type_aliases(content)

        return definitions

    def _extract_package(self, content: str) -> str | None:
        """Extract package declaration."""
        match = re.search(r"^package\s+([\w.]+)", content, re.MULTILINE)
        return match.group(1) if match else None

    def _extract_imports(self, content: str) -> list[str]:
        """Extract import statements."""
        imports = []
        for match in re.finditer(r"^import\s+([\w.]+)", content, re.MULTILINE):
            imports.append(match.group(1))
        return imports

    def _extract_classes(self, content: str) -> list[KotlinClass]:
        """Extract class definitions."""
        classes = []

        class_pattern = r"((?:data|sealed)\s+)?((?:abstract|open|final|inner)\s+)*class\s+([A-Z]\w*)(?:\s*<[^>]+>)?\s*(?:\(([^)]*)\))?\s*(?::\s*([^{]+))?\s*\{"

        line_num = 0
        for line_num, line in enumerate(content.split("\n"), 1):
            match = re.search(class_pattern, line)
            if match:
                modifiers_str = match.group(1) or ""
                visibility_str = match.group(2) or ""
                class_name = match.group(3)
                superclass_str = match.group(5) or ""

                modifiers = self._parse_modifiers(modifiers_str + " " + visibility_str)
                superclass, interfaces = self._parse_superclass_and_interfaces(
                    superclass_str
                )

                kotlin_class = KotlinClass(
                    name=class_name,
                    modifiers=modifiers,
                    superclass=superclass,
                    interfaces=interfaces,
                    line_number=line_num,
                )

                classes.append(kotlin_class)

        return classes

    def _extract_interfaces(self, content: str) -> list[KotlinInterface]:
        """Extract interface definitions."""
        interfaces = []

        interface_pattern = (
            r"interface\s+([A-Z]\w*)(?:\s*<[^>]+>)?\s*(?::\s*([^{]+))?\s*\{"
        )

        for line_num, line in enumerate(content.split("\n"), 1):
            match = re.search(interface_pattern, line)
            if match:
                interface_name = match.group(1)
                superinterfaces_str = match.group(2) or ""

                superinterfaces = [
                    s.strip() for s in superinterfaces_str.split(",") if s.strip()
                ]

                kotlin_interface = KotlinInterface(
                    name=interface_name,
                    superinterfaces=superinterfaces,
                    line_number=line_num,
                )

                interfaces.append(kotlin_interface)

        return interfaces

    def _extract_functions(self, content: str) -> list[KotlinFunction]:
        """Extract top-level function definitions."""
        functions = []

        func_pattern = r"(?:(suspend)\s+)?(?:(inline|infix)\s+)*fun\s+([a-z_]\w*)\s*\(([^)]*)\)\s*(?::\s*([^{=]+))?"

        for line_num, line in enumerate(content.split("\n"), 1):
            match = re.search(func_pattern, line)
            if match:
                is_suspend = bool(match.group(1))
                modifiers_str = match.group(2) or ""
                function_name = match.group(3)
                params_str = match.group(4)
                return_type = match.group(5)

                modifiers = self._parse_modifiers(modifiers_str)
                parameters = self._parse_parameters(params_str)

                kotlin_function = KotlinFunction(
                    name=function_name,
                    parameters=parameters,
                    return_type=return_type.strip() if return_type else None,
                    modifiers=modifiers,
                    is_suspend=is_suspend,
                    is_infix=KotlinModifier.INFIX in modifiers,
                    line_number=line_num,
                )

                functions.append(kotlin_function)

        return functions

    def _extract_extension_functions(
        self, content: str
    ) -> list[KotlinExtensionFunction]:
        """Extract extension functions."""
        extensions = []

        ext_pattern = (
            r"fun\s+([A-Za-z_]\w*)\s*\.\s*([a-z_]\w*)\s*\(([^)]*)\)\s*(?::\s*([^{=]+))?"
        )

        for line_num, line in enumerate(content.split("\n"), 1):
            match = re.search(ext_pattern, line)
            if match:
                receiver_type = match.group(1)
                ext_name = match.group(2)
                params_str = match.group(3)
                return_type = match.group(4)

                parameters = self._parse_parameters(params_str)

                ext_func = KotlinExtensionFunction(
                    name=ext_name,
                    receiver_type=receiver_type,
                    parameters=parameters,
                    return_type=return_type.strip() if return_type else None,
                    line_number=line_num,
                )

                extensions.append(ext_func)

        return extensions

    def _extract_enums(self, content: str) -> list[str]:
        """Extract enum definitions."""
        enums = []

        enum_pattern = r"enum\s+class\s+([A-Z]\w*)"

        for match in re.finditer(enum_pattern, content):
            enums.append(match.group(1))

        return enums

    def _extract_type_aliases(self, content: str) -> dict[str, str]:
        """Extract type aliases."""
        aliases = {}

        alias_pattern = r"typealias\s+([A-Z]\w*)\s*=\s*([^;\n]+)"

        for match in re.finditer(alias_pattern, content):
            alias_name = match.group(1)
            real_type = match.group(2).strip()
            aliases[alias_name] = real_type

        return aliases

    def _parse_modifiers(self, modifiers_str: str) -> list[KotlinModifier]:
        """Parse modifier string into KotlinModifier list."""
        modifiers = []
        modifier_keywords = [m.value for m in KotlinModifier]

        for keyword in modifier_keywords:
            if re.search(rf"\b{keyword}\b", modifiers_str):
                modifiers.append(KotlinModifier(keyword))

        return modifiers

    def _parse_parameters(self, params_str: str) -> list[KotlinParameter]:
        """Parse parameter string into KotlinParameter list."""
        parameters = []

        if not params_str or not params_str.strip():
            return parameters

        param_parts = []
        current = ""
        depth = 0

        for char in params_str:
            if char in "<(":
                depth += 1
            elif char in ">)":
                depth -= 1
            elif char == "," and depth == 0:
                param_parts.append(current)
                current = ""
                continue
            current += char

        if current:
            param_parts.append(current)

        for part in param_parts:
            part = part.strip()
            if not part:
                continue

            is_vararg = part.startswith("vararg")
            if is_vararg:
                part = part[6:].strip()

            if ":" in part:
                name_part, type_part = part.split(":", 1)
                name = name_part.strip()

                if "=" in type_part:
                    type_str, default = type_part.split("=", 1)
                    param_type = type_str.strip()
                    default_value = default.strip()
                else:
                    param_type = type_part.strip()
                    default_value = None

                parameters.append(
                    KotlinParameter(
                        name=name,
                        type=param_type,
                        default_value=default_value,
                        is_vararg=is_vararg,
                    )
                )
            else:
                parameters.append(KotlinParameter(name=part, is_vararg=is_vararg))

        return parameters

    def _parse_superclass_and_interfaces(self, superclass_str: str) -> tuple:
        """Parse superclass and interfaces from string."""
        if not superclass_str:
            return None, []

        parts = [p.strip() for p in superclass_str.split(",")]

        superclass = None
        interfaces = []

        for part in parts:
            if not superclass and part and part[0].isupper():
                superclass = part
            elif part:
                interfaces.append(part)

        return superclass, interfaces

    def analyze_kotlin_file(self, file_path: str) -> dict:
        """
        Complete analysis of a Kotlin file.

        Args:
            file_path: Path to Kotlin file

        Returns:
            Dictionary with comprehensive analysis
        """
        path = Path(file_path)
        content = path.read_text(encoding="utf-8", errors="ignore")

        analysis = {
            "file_path": str(file_path),
            "definitions": self.extract_kotlin_definitions(content, file_path),
            "file_size_lines": len(content.split("\n")),
        }

        return analysis

    def extract_coroutine_patterns(self, content: str) -> list[dict[str, str]]:
        """
        Extract coroutine-related patterns from Kotlin.

        Args:
            content: Kotlin source code

        Returns:
            List of coroutine patterns (launch, async, withContext, etc.)
        """
        patterns = []

        coroutine_keywords = [
            "launch",
            "async",
            "withContext",
            "runBlocking",
            "coroutineScope",
        ]

        for keyword in coroutine_keywords:
            pattern = rf"\b{keyword}\s*\{{"
            for match in re.finditer(pattern, content):
                patterns.append(
                    {
                        "type": keyword,
                        "position": match.start(),
                    }
                )

        return patterns

    def extract_dsl_builders(self, content: str) -> list[dict[str, str]]:
        """
        Extract DSL builder patterns from Kotlin.

        Args:
            content: Kotlin source code

        Returns:
            List of DSL builders (e.g., html { }, json { })
        """
        builders = []

        builder_pattern = r"([a-z_]\w*)\s*\{(?:[^{}]++|(?:\{[^{}]*+\}))*+\}"

        for match in re.finditer(builder_pattern, content):
            builders.append(
                {
                    "name": match.group(1),
                    "position": match.start(),
                }
            )

        return builders
