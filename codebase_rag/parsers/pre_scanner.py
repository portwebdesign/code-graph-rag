from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from codebase_rag.core import constants as cs
from codebase_rag.infrastructure.language_spec import get_language_spec_for_path

from ..utils.path_utils import should_skip_path


@dataclass
class PreScanIndex:
    """
    Index mapping symbols to the modules that define them, and vice-versa.

    Attributes:
        symbol_to_modules (dict[str, set[str]]): Map of symbol name -> set of module qualified names.
        module_to_symbols (dict[str, set[str]]): Map of module qualified name -> set of symbol names.
    """

    symbol_to_modules: dict[str, set[str]] = field(default_factory=dict)

    symbol_to_modules: dict[str, set[str]] = field(default_factory=dict)
    module_to_symbols: dict[str, set[str]] = field(default_factory=dict)

    def add(self, module_qn: str, symbol: str) -> None:
        """Add a symbol to the module and reverse indexes.

        Args:
            module_qn: Fully qualified module name.
            symbol: Symbol name to index.

        Returns:
            None.
        """
        if not symbol:
            return
        self.module_to_symbols.setdefault(module_qn, set()).add(symbol)
        self.symbol_to_modules.setdefault(symbol, set()).add(module_qn)


class PreScanner:
    """
    Scans the repository to identify which modules define which symbols.

    This class performs a lightweight first pass over the repository to build a
    mapping of symbols (classes, functions) to the modules that define them.
    This index is essential for resolving imports and dependencies in the
    subsequent full parsing phase.

    Attributes:
        repo_path (Path): The root path of the repository.
        project_name (str): The name of the project.
        exclude_paths (frozenset[str] | None): Set of paths to exclude from scanning.
        unignore_paths (frozenset[str] | None): Set of paths to explicitly include.
    """

    def __init__(
        self,
        repo_path: Path,
        project_name: str,
        exclude_paths: frozenset[str] | None = None,
        unignore_paths: frozenset[str] | None = None,
    ) -> None:
        """Initialize the pre-scanner.

        Args:
            repo_path: Root path of the repository to scan.
            project_name: Project name used in qualified module paths.
            exclude_paths: Optional ignore patterns for paths to skip.
            unignore_paths: Optional patterns to re-include paths.

        Returns:
            None.
        """
        self.repo_path = repo_path
        self.project_name = project_name
        self.exclude_paths = exclude_paths
        self.unignore_paths = unignore_paths

    def scan_repo(self) -> PreScanIndex:
        """Scan the repository and build a symbol-to-module index.

        Args:
            None.

        Returns:
            PreScanIndex containing module and symbol mappings.
        """
        index = PreScanIndex()

        for file_path in self.repo_path.rglob("*"):
            if not file_path.is_file():
                continue
            if should_skip_path(
                file_path,
                self.repo_path,
                exclude_paths=self.exclude_paths,
                unignore_paths=self.unignore_paths,
            ):
                continue

            lang_spec = get_language_spec_for_path(file_path)
            if not lang_spec or not isinstance(
                lang_spec.language, cs.SupportedLanguage
            ):
                continue

            symbols = self._scan_file(file_path, lang_spec.language)
            if not symbols:
                continue
            module_qn = self._module_qn_for_path(file_path)
            for symbol in symbols:
                index.add(module_qn, symbol)

        logger.info(
            "Pre-scan complete: {} modules, {} symbols",
            len(index.module_to_symbols),
            len(index.symbol_to_modules),
        )
        return index

    def _scan_file(self, file_path: Path, language: cs.SupportedLanguage) -> set[str]:
        """Extract symbols from a single file for a given language.

        Args:
            file_path: File path to scan.
            language: Language identifier used to select a scanner.

        Returns:
            Set of symbol names extracted from the file.
        """
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return set()

        match language:
            case cs.SupportedLanguage.JS | cs.SupportedLanguage.TS:
                return self._scan_js_ts(text)
            case cs.SupportedLanguage.PYTHON:
                return self._scan_python(text)
            case cs.SupportedLanguage.GO:
                return self._scan_go(text)
            case cs.SupportedLanguage.CSHARP:
                return self._scan_csharp(text)
            case cs.SupportedLanguage.PHP:
                return self._scan_php(text)
            case cs.SupportedLanguage.RUST:
                return self._scan_rust(text)
            case _:
                return set()

    @staticmethod
    def _scan_js_ts(text: str) -> set[str]:
        """Extract exported symbols from JavaScript or TypeScript source.

        Args:
            text: File contents.

        Returns:
            Set of exported symbol names.
        """
        symbols: set[str] = set()
        export_decl = re.compile(
            r"\bexport\s+(?:default\s+)?"
            r"(?:function|class|const|let|var|interface|type|enum)\s+([A-Za-z_][\w]*)",
            re.MULTILINE,
        )
        for match in export_decl.findall(text):
            symbols.add(match)

        export_list = re.compile(r"\bexport\s*\{([^}]+)\}", re.MULTILINE)
        for block in export_list.findall(text):
            for entry in block.split(","):
                entry = entry.strip()
                if not entry:
                    continue
                if " as " in entry:
                    entry = entry.split(" as ", 1)[-1].strip()
                entry = entry.split(" ", 1)[0].strip()
                if entry:
                    symbols.add(entry)

        if re.search(r"\bexport\s+default\b", text):
            symbols.add("default")

        return symbols

    @staticmethod
    def _scan_python(text: str) -> set[str]:
        """Extract top-level class and function symbols from Python source.

        Args:
            text: File contents.

        Returns:
            Set of symbol names.
        """
        symbols: set[str] = set()
        for match in re.findall(r"^\s*def\s+([A-Za-z_][\w]*)", text, re.MULTILINE):
            symbols.add(match)
        for match in re.findall(r"^\s*class\s+([A-Za-z_][\w]*)", text, re.MULTILINE):
            symbols.add(match)
        return symbols

    @staticmethod
    def _scan_go(text: str) -> set[str]:
        """Extract top-level symbols from Go source.

        Args:
            text: File contents.

        Returns:
            Set of symbol names.
        """
        symbols: set[str] = set()
        patterns = [
            r"^\s*func\s+([A-Za-z_][\w]*)",
            r"^\s*type\s+([A-Za-z_][\w]*)",
            r"^\s*var\s+([A-Za-z_][\w]*)",
            r"^\s*const\s+([A-Za-z_][\w]*)",
        ]
        for pattern in patterns:
            for match in re.findall(pattern, text, re.MULTILINE):
                symbols.add(match)
        return symbols

    @staticmethod
    def _scan_csharp(text: str) -> set[str]:
        """Extract type and method symbols from C# source.

        Args:
            text: File contents.

        Returns:
            Set of symbol names.
        """
        symbols: set[str] = set()
        type_pattern = re.compile(
            r"\b(class|interface|struct|record|enum)\s+([A-Za-z_][\w]*)",
            re.MULTILINE,
        )
        for _, name in type_pattern.findall(text):
            symbols.add(name)
        method_pattern = re.compile(
            r"\b(?:public|internal|private|protected|static|virtual|override|async|sealed|new)\s+"  # noqa: E501
            r"[A-Za-z_][\w<>\[\]]*\s+([A-Za-z_][\w]*)\s*\(",
            re.MULTILINE,
        )
        for name in method_pattern.findall(text):
            symbols.add(name)
        return symbols

    @staticmethod
    def _scan_php(text: str) -> set[str]:
        """Extract class, interface, trait, and function symbols from PHP source.

        Args:
            text: File contents.

        Returns:
            Set of symbol names.
        """
        symbols: set[str] = set()
        for match in re.findall(r"\bfunction\s+([A-Za-z_][\w]*)", text):
            symbols.add(match)
        for match in re.findall(r"\bclass\s+([A-Za-z_][\w]*)", text):
            symbols.add(match)
        for match in re.findall(r"\binterface\s+([A-Za-z_][\w]*)", text):
            symbols.add(match)
        for match in re.findall(r"\btrait\s+([A-Za-z_][\w]*)", text):
            symbols.add(match)
        return symbols

    @staticmethod
    def _scan_rust(text: str) -> set[str]:
        """Extract top-level symbols from Rust source.

        Args:
            text: File contents.

        Returns:
            Set of symbol names.
        """
        symbols: set[str] = set()
        for match in re.findall(r"\bfn\s+([A-Za-z_][\w]*)", text):
            symbols.add(match)
        for match in re.findall(r"\bstruct\s+([A-Za-z_][\w]*)", text):
            symbols.add(match)
        for match in re.findall(r"\benum\s+([A-Za-z_][\w]*)", text):
            symbols.add(match)
        for match in re.findall(r"\btrait\s+([A-Za-z_][\w]*)", text):
            symbols.add(match)
        return symbols

    def _module_qn_for_path(self, file_path: Path) -> str:
        """Build a qualified module name for a given file path.

        Args:
            file_path: File path to convert.

        Returns:
            Fully qualified module name for the file.
        """
        relative_path = file_path.relative_to(self.repo_path)
        parts = list(relative_path.with_suffix("").parts)
        if file_path.name in (cs.INIT_PY, cs.MOD_RS):
            parts = list(relative_path.parent.parts)
        return cs.SEPARATOR_DOT.join([self.project_name, *parts])
