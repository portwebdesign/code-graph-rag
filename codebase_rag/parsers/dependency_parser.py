"""
This module provides parsers for various dependency management files from different
programming language ecosystems.

It defines a base `DependencyParser` class and specific implementations for
different file types, such as `pyproject.toml`, `requirements.txt`, `package.json`,
`Cargo.toml`, and more. The main entry point is the `parse_dependencies` function,
which dispatches to the appropriate parser based on the file name.

Each parser is responsible for reading a dependency file and extracting a list
of `Dependency` objects, which contain the name and version specifier of each
dependency.
"""

import json
import re
from pathlib import Path

import defusedxml.ElementTree as ET
import toml
from loguru import logger

from codebase_rag.data_models.models import Dependency

from ..core import constants as cs
from ..core import logs as ls


def _extract_pep508_package_name(dep_string: str) -> tuple[str, str]:
    """
    Extracts a package name and version specifier from a PEP 508 string.

    Args:
        dep_string (str): The dependency string (e.g., 'requests[security]>=2.25.1').

    Returns:
        tuple[str, str]: A tuple containing the package name and the version specifier.
    """
    stripped = dep_string.strip()
    match = re.match(r"^([a-zA-Z0-9_.-]+(?:\[[^\]]*\])?)", stripped)
    if not match:
        return "", ""
    name_with_extras = match[1]
    name_match = re.match(r"^([a-zA-Z0-9_.-]+)", name_with_extras)
    if not name_match:
        return "", ""
    name = name_match[1]
    spec = stripped[len(name_with_extras) :].strip()
    return name, spec


class DependencyParser:
    """Base class for dependency file parsers."""

    def parse(self, file_path: Path) -> list[Dependency]:
        """
        Parses a dependency file to extract dependencies.

        Args:
            file_path (Path): The path to the dependency file.

        Returns:
            list[Dependency]: A list of extracted Dependency objects.
        """
        raise NotImplementedError


class PyProjectTomlParser(DependencyParser):
    """Parses `pyproject.toml` files for dependencies."""

    def parse(self, file_path: Path) -> list[Dependency]:
        """
        Parses a `pyproject.toml` file for both standard and optional dependencies.

        Args:
            file_path (Path): The path to the `pyproject.toml` file.

        Returns:
            list[Dependency]: A list of extracted dependencies.
        """
        dependencies: list[Dependency] = []
        try:
            data = toml.load(file_path)

            if poetry_deps := (
                data.get(cs.DEP_KEY_TOOL, {})
                .get(cs.DEP_KEY_POETRY, {})
                .get(cs.DEP_KEY_DEPENDENCIES, {})
            ):
                dependencies.extend(
                    Dependency(dep_name, str(dep_spec))
                    for dep_name, dep_spec in poetry_deps.items()
                    if dep_name.lower() != cs.DEP_EXCLUDE_PYTHON
                )
            if project_deps := data.get(cs.DEP_KEY_PROJECT, {}).get(
                cs.DEP_KEY_DEPENDENCIES, []
            ):
                for dep_line in project_deps:
                    dep_name, _ = _extract_pep508_package_name(dep_line)
                    if dep_name:
                        dependencies.append(Dependency(dep_name, dep_line))

            optional_deps = data.get(cs.DEP_KEY_PROJECT, {}).get(
                cs.DEP_KEY_OPTIONAL_DEPS, {}
            )
            for group_name, deps in optional_deps.items():
                for dep_line in deps:
                    dep_name, _ = _extract_pep508_package_name(dep_line)
                    if dep_name:
                        dependencies.append(
                            Dependency(
                                dep_name, dep_line, {cs.DEP_KEY_GROUP: group_name}
                            )
                        )
        except Exception as e:
            logger.error(ls.DEP_PARSE_ERROR_PYPROJECT.format(path=file_path, error=e))
        return dependencies


class RequirementsTxtParser(DependencyParser):
    """Parses `requirements.txt` files."""

    def parse(self, file_path: Path) -> list[Dependency]:
        """
        Parses a `requirements.txt` file.

        Args:
            file_path (Path): The path to the `requirements.txt` file.

        Returns:
            list[Dependency]: A list of extracted dependencies.
        """
        dependencies: list[Dependency] = []
        try:
            with open(file_path, encoding=cs.ENCODING_UTF8) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("-"):
                        continue

                    dep_name, version_spec = _extract_pep508_package_name(line)
                    if dep_name:
                        dependencies.append(Dependency(dep_name, version_spec))
        except Exception as e:
            logger.error(
                ls.DEP_PARSE_ERROR_REQUIREMENTS.format(path=file_path, error=e)
            )
        return dependencies


class PackageJsonParser(DependencyParser):
    """Parses `package.json` files for Node.js projects."""

    def parse(self, file_path: Path) -> list[Dependency]:
        """
        Parses a `package.json` file.

        Args:
            file_path (Path): The path to the `package.json` file.

        Returns:
            list[Dependency]: A list of extracted dependencies.
        """
        dependencies: list[Dependency] = []
        try:
            self._load_and_collect_deps(file_path, dependencies)
        except Exception as e:
            logger.error(
                ls.DEP_PARSE_ERROR_PACKAGE_JSON.format(path=file_path, error=e)
            )
        return dependencies

    def _load_and_collect_deps(
        self, file_path: Path, dependencies: list[Dependency]
    ) -> None:
        """Loads the JSON and collects dependencies from different sections."""
        with open(file_path, encoding=cs.ENCODING_UTF8) as f:
            data = json.load(f)

        for key in (
            cs.DEP_KEY_DEPENDENCIES,
            cs.DEP_KEY_DEV_DEPS_JSON,
            cs.DEP_KEY_PEER_DEPS,
        ):
            dependencies.extend(
                Dependency(dep_name, dep_spec)
                for dep_name, dep_spec in data.get(key, {}).items()
            )


class CargoTomlParser(DependencyParser):
    """Parses `Cargo.toml` files for Rust projects."""

    def parse(self, file_path: Path) -> list[Dependency]:
        """
        Parses a `Cargo.toml` file.

        Args:
            file_path (Path): The path to the `Cargo.toml` file.

        Returns:
            list[Dependency]: A list of extracted dependencies.
        """
        dependencies: list[Dependency] = []
        try:
            data = toml.load(file_path)

            deps = data.get(cs.DEP_KEY_DEPENDENCIES, {})
            for dep_name, dep_spec in deps.items():
                version = (
                    dep_spec
                    if isinstance(dep_spec, str)
                    else dep_spec.get(cs.DEP_KEY_VERSION, "")
                )
                dependencies.append(Dependency(dep_name, version))

            dev_deps = data.get(cs.DEP_KEY_DEV_DEPENDENCIES, {})
            for dep_name, dep_spec in dev_deps.items():
                version = (
                    dep_spec
                    if isinstance(dep_spec, str)
                    else dep_spec.get(cs.DEP_KEY_VERSION, "")
                )
                dependencies.append(Dependency(dep_name, version))
        except Exception as e:
            logger.error(ls.DEP_PARSE_ERROR_CARGO.format(path=file_path, error=e))
        return dependencies


class GoModParser(DependencyParser):
    """Parses `go.mod` files for Go projects."""

    def parse(self, file_path: Path) -> list[Dependency]:
        """
        Parses a `go.mod` file.

        Args:
            file_path (Path): The path to the `go.mod` file.

        Returns:
            list[Dependency]: A list of extracted dependencies.
        """
        dependencies: list[Dependency] = []
        try:
            with open(file_path, encoding=cs.ENCODING_UTF8) as f:
                in_require_block = False
                for line in f:
                    line = line.strip()

                    if line.startswith(cs.GOMOD_REQUIRE_BLOCK_START):
                        in_require_block = True
                        continue
                    elif line == cs.GOMOD_BLOCK_END and in_require_block:
                        in_require_block = False
                        continue
                    elif (
                        line.startswith(cs.GOMOD_REQUIRE_LINE_PREFIX)
                        and not in_require_block
                    ):
                        parts = line.split()[1:]
                        if len(parts) >= 2:
                            dependencies.append(Dependency(parts[0], parts[1]))
                    elif (
                        in_require_block
                        and line
                        and not line.startswith(cs.GOMOD_COMMENT_PREFIX)
                    ):
                        parts = line.split()
                        if len(parts) >= 2:
                            dep_name = parts[0]
                            version = parts[1]
                            if not version.startswith(cs.GOMOD_COMMENT_PREFIX):
                                dependencies.append(Dependency(dep_name, version))
        except Exception as e:
            logger.error(ls.DEP_PARSE_ERROR_GOMOD.format(path=file_path, error=e))
        return dependencies


class GemfileParser(DependencyParser):
    """Parses `Gemfile` files for Ruby projects."""

    def parse(self, file_path: Path) -> list[Dependency]:
        """
        Parses a `Gemfile`.

        Args:
            file_path (Path): The path to the `Gemfile`.

        Returns:
            list[Dependency]: A list of extracted dependencies.
        """
        dependencies: list[Dependency] = []
        try:
            with open(file_path, encoding=cs.ENCODING_UTF8) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith(cs.GEMFILE_GEM_PREFIX):
                        if gem_match := re.match(
                            r'gem\s+["\']([^"\']+)["\'](?:\s*,\s*["\']([^"\']+)["\'])?',
                            line,
                        ):
                            dep_name = gem_match[1]
                            version = gem_match[2] or ""
                            dependencies.append(Dependency(dep_name, version))
        except Exception as e:
            logger.error(ls.DEP_PARSE_ERROR_GEMFILE.format(path=file_path, error=e))
        return dependencies


class ComposerJsonParser(DependencyParser):
    """Parses `composer.json` files for PHP projects."""

    def parse(self, file_path: Path) -> list[Dependency]:
        """
        Parses a `composer.json` file.

        Args:
            file_path (Path): The path to the `composer.json` file.

        Returns:
            list[Dependency]: A list of extracted dependencies.
        """
        dependencies: list[Dependency] = []
        try:
            with open(file_path, encoding=cs.ENCODING_UTF8) as f:
                data = json.load(f)

            deps = data.get(cs.DEP_KEY_REQUIRE, {})
            dependencies.extend(
                Dependency(dep_name, dep_spec)
                for dep_name, dep_spec in deps.items()
                if dep_name != cs.DEP_EXCLUDE_PHP
            )
            dev_deps = data.get(cs.DEP_KEY_REQUIRE_DEV, {})
            dependencies.extend(
                Dependency(dep_name, dep_spec)
                for dep_name, dep_spec in dev_deps.items()
            )
        except Exception as e:
            logger.error(ls.DEP_PARSE_ERROR_COMPOSER.format(path=file_path, error=e))
        return dependencies


class CsprojParser(DependencyParser):
    """Parses `.csproj` files for .NET projects."""

    def parse(self, file_path: Path) -> list[Dependency]:
        """
        Parses a `.csproj` file for `PackageReference` items.

        Args:
            file_path (Path): The path to the `.csproj` file.

        Returns:
            list[Dependency]: A list of extracted dependencies.
        """
        dependencies: list[Dependency] = []
        try:
            tree = ET.parse(file_path)
            root = tree.getroot()

            for pkg_ref in root.iter(cs.DEP_XML_PACKAGE_REF):
                include = pkg_ref.get(cs.DEP_ATTR_INCLUDE)
                version = pkg_ref.get(cs.DEP_ATTR_VERSION)

                if include:
                    dependencies.append(Dependency(include, version or ""))
        except Exception as e:
            logger.error(ls.DEP_PARSE_ERROR_CSPROJ.format(path=file_path, error=e))
        return dependencies


def parse_dependencies(file_path: Path) -> list[Dependency]:
    """
    Selects the appropriate parser based on the file name and parses it.

    Args:
        file_path (Path): The path to the dependency file.

    Returns:
        list[Dependency]: A list of extracted dependencies.
    """
    file_name = file_path.name.lower()

    match file_name:
        case cs.DEP_FILE_PYPROJECT:
            return PyProjectTomlParser().parse(file_path)
        case cs.DEP_FILE_REQUIREMENTS:
            return RequirementsTxtParser().parse(file_path)
        case cs.DEP_FILE_PACKAGE_JSON:
            return PackageJsonParser().parse(file_path)
        case cs.DEP_FILE_CARGO:
            return CargoTomlParser().parse(file_path)
        case cs.DEP_FILE_GOMOD:
            return GoModParser().parse(file_path)
        case cs.DEP_FILE_GEMFILE:
            return GemfileParser().parse(file_path)
        case cs.DEP_FILE_COMPOSER:
            return ComposerJsonParser().parse(file_path)
        case _ if file_path.suffix.lower() == cs.CSPROJ_SUFFIX:
            return CsprojParser().parse(file_path)
        case _:
            return []
