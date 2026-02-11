from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import cast

from tree_sitter import Node, Query, QueryCursor

from codebase_rag.core import constants as cs
from codebase_rag.data_models.types_defs import LanguageQueries
from codebase_rag.services import IngestorProtocol

_SCM_LANGUAGE_ALIAS: dict[cs.SupportedLanguage, str] = {
    cs.SupportedLanguage.TS: "javascript",
}


def _parse_scm_queries(scm_text: str) -> dict[str, str]:
    """Parses SCM text to extract named queries."""
    queries: dict[str, str] = {}
    query_pattern = r";\s*@query:\s*(\w+)\s*\n((?:(?!;\s*@query:)[\s\S])*)"

    for match in re.finditer(query_pattern, scm_text):
        query_name = match.group(1).strip()
        query_string = match.group(2).strip()
        if query_string:
            queries[query_name] = query_string

    return queries


class TailwindUsageProcessor:
    """
    Processes Tailwind CSS usage in source files.

    This class scans files for Tailwind class attributes (e.g., `class="..."`, `className="..."`)
    and Tailwind directives in CSS files (e.g., `@apply`, `@tailwind`). It extracts
    utility names and creates relationships between modules and the utilities they use.
    """

    def __init__(
        self,
        ingestor: IngestorProtocol,
        repo_path: Path,
        project_name: str,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> None:
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.project_name = project_name
        self.queries = queries
        self._compiled_queries: dict[tuple[cs.SupportedLanguage, str], Query] = {}
        self._source_inline: set[str] = set()
        self._tailwind_asset_qn: str | None = None
        self._tailwind_config_qn: str | None = None

    def process_ast_cache(
        self, ast_items: Iterable[tuple[Path, tuple[object, cs.SupportedLanguage]]]
    ) -> None:
        """
        Process cached ASTs to find Tailwind usage.

        Args:
            ast_items (Iterable): Iterable of (path, (root, language)) tuples.
        """
        for file_path, (root_node, language) in ast_items:
            self.process_file(file_path, cast(Node, root_node), language)

        self._ingest_tailwind_config_metadata()

    def process_file(
        self,
        file_path: Path,
        root_node: Node,
        language: cs.SupportedLanguage,
    ) -> None:
        """
        Process a single file for Tailwind classes and directives.

        Args:
            file_path (Path): Path to the file.
            root_node (object): Root AST node.
            language (cs.SupportedLanguage): Programming language.
        """
        if language not in {
            cs.SupportedLanguage.HTML,
            cs.SupportedLanguage.JS,
            cs.SupportedLanguage.TS,
            cs.SupportedLanguage.VUE,
            cs.SupportedLanguage.SVELTE,
            cs.SupportedLanguage.CSS,
            cs.SupportedLanguage.SCSS,
        }:
            return

        try:
            source_text = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return

        if language in {
            cs.SupportedLanguage.HTML,
            cs.SupportedLanguage.JS,
            cs.SupportedLanguage.TS,
            cs.SupportedLanguage.VUE,
            cs.SupportedLanguage.SVELTE,
        }:
            self._process_class_attributes(file_path, root_node, language, source_text)

        if language in {cs.SupportedLanguage.CSS, cs.SupportedLanguage.SCSS}:
            self._process_tailwind_at_rules(file_path, root_node, language, source_text)

    def _process_class_attributes(
        self,
        file_path: Path,
        root_node: Node,
        language: cs.SupportedLanguage,
        source_text: str,
    ) -> None:
        """
        Extract Tailwind classes from HTML/JSX class attributes.

        Args:
            file_path (Path): Path to the file.
            root_node (object): Root AST node.
            language (cs.SupportedLanguage): Language.
            source_text (str): File content source.
        """
        query = self._get_query(language, "tailwind_class_attributes")
        if not query:
            return

        cursor = QueryCursor(query)
        captures = cursor.captures(root_node)
        class_nodes = [
            node for node, name in captures if name == "tailwind_class_value"
        ]
        expr_nodes = [node for node, name in captures if name == "tailwind_class_expr"]
        if not class_nodes and not expr_nodes:
            return

        module_qn = self._module_qn_for_path(file_path)
        tailwind_used = False

        for node in class_nodes:
            raw_text = self._slice_text(
                source_text, cast(Node, node).start_byte, cast(Node, node).end_byte
            )
            class_text = self._strip_quotes(raw_text)
            for utility in self._extract_classes_from_value(class_text):
                utility = self._normalize_utility(utility)
                if not utility:
                    continue
                tailwind_used = True
                utility_qn = self._ensure_utility_node(utility)
                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
                    cs.RelationshipType.USES_UTILITY,
                    (cs.NodeLabel.TAILWIND_UTILITY, cs.KEY_QUALIFIED_NAME, utility_qn),
                    {cs.KEY_RELATION_TYPE: "tailwind"},
                )

        for node in expr_nodes:
            raw_text = self._slice_text(
                source_text, cast(Node, node).start_byte, cast(Node, node).end_byte
            )
            for utility in self._extract_classes_from_expression(raw_text):
                utility = self._normalize_utility(utility)
                if not utility:
                    continue
                tailwind_used = True
                utility_qn = self._ensure_utility_node(utility)
                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
                    cs.RelationshipType.USES_UTILITY,
                    (cs.NodeLabel.TAILWIND_UTILITY, cs.KEY_QUALIFIED_NAME, utility_qn),
                    {cs.KEY_RELATION_TYPE: "tailwind"},
                )

        if tailwind_used:
            self._ensure_tailwind_asset()
            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
                cs.RelationshipType.USES_ASSET,
                (cs.NodeLabel.ASSET, cs.KEY_QUALIFIED_NAME, self._tailwind_asset_qn),
                {cs.KEY_RELATION_TYPE: "tailwind"},
            )

    def _process_tailwind_at_rules(
        self,
        file_path: Path,
        root_node: Node,
        language: cs.SupportedLanguage,
        source_text: str,
    ) -> None:
        """
        Process Tailwind directives in CSS/SCSS files.

        Args:
            file_path (Path): Path to the file.
            root_node (object): Root AST node.
            language (cs.SupportedLanguage): Language (CSS/SCSS).
            source_text (str): File content source.
        """
        query = self._get_query(language, "tailwind_at_rules")
        if not query:
            return

        cursor = QueryCursor(query)
        captures = cursor.captures(root_node)
        rule_nodes = [node for node, name in captures if name == "tailwind_at_rule"]
        if not rule_nodes:
            return

        module_qn = self._module_qn_for_path(file_path)
        tailwind_used = False

        for node in rule_nodes:
            rule_text = self._slice_text(
                source_text, cast(Node, node).start_byte, cast(Node, node).end_byte
            )
            if "@apply" in rule_text:
                for utility in self._parse_apply_utilities(rule_text):
                    utility = self._normalize_utility(utility)
                    if not utility:
                        continue
                    tailwind_used = True
                    utility_qn = self._ensure_utility_node(utility)
                    self.ingestor.ensure_relationship_batch(
                        (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
                        cs.RelationshipType.USES_UTILITY,
                        (
                            cs.NodeLabel.TAILWIND_UTILITY,
                            cs.KEY_QUALIFIED_NAME,
                            utility_qn,
                        ),
                        {cs.KEY_RELATION_TYPE: "tailwind_apply"},
                    )

            if "@source" in rule_text:
                for inline in self._parse_source_inline(rule_text):
                    self._source_inline.add(inline)

            if "@tailwind" in rule_text or "@layer" in rule_text:
                tailwind_used = True

        if tailwind_used:
            self._ensure_tailwind_asset()
            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
                cs.RelationshipType.USES_ASSET,
                (cs.NodeLabel.ASSET, cs.KEY_QUALIFIED_NAME, self._tailwind_asset_qn),
                {cs.KEY_RELATION_TYPE: "tailwind"},
            )

    def _ingest_tailwind_config_metadata(self) -> None:
        """
        Ingest metadata from tailwind.config.* files and inline config.
        """
        config_paths = list(self.repo_path.rglob("tailwind.config.*"))
        if not config_paths and not self._source_inline:
            return

        content_entries: set[str] = set()
        safelist_entries: set[str] = set()
        config_rel_paths: list[str] = []

        for path in config_paths:
            config_rel_paths.append(str(path.relative_to(self.repo_path)))
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            content_entries.update(self._extract_config_list(text, "content"))
            safelist_entries.update(self._extract_config_list(text, "safelist"))
            content_entries.update(
                self._extract_config_nested_list(text, "content", "files")
            )

        self._ensure_tailwind_config_asset(
            config_rel_paths,
            sorted(content_entries),
            sorted(safelist_entries),
            sorted(self._source_inline),
        )

    def _extract_config_list(self, text: str, key: str) -> list[str]:
        """
        Extract a list of strings from a config key (e.g., 'content').
        """
        pattern = rf"{key}\s*:\s*(\[[\s\S]*?\])"
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            return []
        return self._extract_string_literals(match.group(1))

    def _extract_config_nested_list(
        self, text: str, key: str, nested_key: str
    ) -> list[str]:
        """
        Extract a nested list of strings (e.g., 'content: { files: [...] }').
        """
        pattern = rf"{key}\s*:\s*\{{[\s\S]*?{nested_key}\s*:\s*(\[[\s\S]*?\])"
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            return []
        return self._extract_string_literals(match.group(1))

    @staticmethod
    def _extract_string_literals(text: str) -> list[str]:
        """
        Extract all string literals (single, double, or backtick quoted) from text.
        """
        values: list[str] = []
        pattern = re.compile(r"'([^']+)'|\"([^\"]+)\"|`([^`]+)`")
        for match in pattern.finditer(text):
            single, double, backtick = match.groups()
            if single:
                values.append(single)
            elif double:
                values.append(double)
            elif backtick is not None:
                values.extend(
                    TailwindUsageProcessor._extract_template_literal_values(backtick)
                )
        return values

    @staticmethod
    def _extract_template_literal_values(text: str) -> list[str]:
        """
        Extract values from a template literal, splitting on logic fragments.
        """
        parts = re.split(r"\$\{[^}]*\}", text)
        classes: list[str] = []
        for part in parts:
            classes.extend([c for c in re.split(r"\s+", part) if c])
        return classes

    @staticmethod
    def _parse_apply_utilities(rule_text: str) -> list[str]:
        """
        Parse utility classes from an @apply directive.
        """
        match = re.search(r"@apply\s+([^;\}]+)", rule_text)
        if not match:
            return []
        return match.group(1).strip().split()

    @staticmethod
    def _parse_source_inline(rule_text: str) -> list[str]:
        """
        Parse @source inline directives.
        """
        return re.findall(
            r"@source\s+inline\(\s*['\"]([^'\"]+)['\"]\s*\)",
            rule_text,
            re.IGNORECASE,
        )

    @staticmethod
    def _strip_quotes(raw_text: str) -> str:
        """
        Remove surrounding quotes from a string if present.
        """
        trimmed = raw_text.strip()
        if (
            len(trimmed) >= 2
            and trimmed[0] == trimmed[-1]
            and trimmed[0] in {"'", '"', "`"}
        ):
            return trimmed[1:-1]
        return trimmed

    @staticmethod
    def _split_class_list(value: str) -> list[str]:
        """
        Split a space-separated string of classes into a list.
        """
        return [part for part in re.split(r"\s+", value) if part]

    def _extract_classes_from_value(self, value: str) -> list[str]:
        """
        Extract utility classes from a raw attribute value string.
        """
        cleaned = value.strip()
        if not cleaned:
            return []

        if cleaned.startswith("`") and cleaned.endswith("`"):
            return self._extract_from_template_literal(cleaned[1:-1])

        if cleaned.startswith("[") and cleaned.endswith("]"):
            return self._extract_string_literals(cleaned)

        if cleaned.startswith("{") and cleaned.endswith("}"):
            return self._extract_object_keys(cleaned)

        return self._split_class_list(cleaned)

    def _extract_classes_from_expression(self, expr: str) -> list[str]:
        """
        Extract utility classes from a JS/TS expression (e.g., classnames(), clsx()).
        """
        cleaned = expr.strip()
        if not cleaned:
            return []

        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = cleaned[1:-1].strip()

        if cleaned.startswith("{") and cleaned.endswith("}"):
            cleaned = cleaned[1:-1].strip()

        if ".join" in cleaned and cleaned.strip().startswith("["):
            cleaned = cleaned.split(".join", 1)[0].strip()

        classes: list[str] = []

        for call_match in re.finditer(
            r"\b(classnames|clsx)\s*\((?P<args>[\s\S]*?)\)", cleaned
        ):
            args = call_match.group("args")
            classes.extend(self._extract_string_literals(args))
            for obj_match in re.finditer(r"\{[^}]*\}", args):
                classes.extend(self._extract_object_keys(obj_match.group(0)))

        if cleaned.startswith("[") and cleaned.endswith("]"):
            classes.extend(self._extract_string_literals(cleaned))
            for obj_match in re.finditer(r"\{[^}]*\}", cleaned):
                classes.extend(self._extract_object_keys(obj_match.group(0)))
            return classes

        if cleaned.startswith("{") and cleaned.endswith("}"):
            classes.extend(self._extract_object_keys(cleaned))
            return classes

        if cleaned.startswith("`") and cleaned.endswith("`"):
            classes.extend(self._extract_from_template_literal(cleaned[1:-1]))
            return classes

        classes.extend(self._extract_string_literals(cleaned))
        for obj_match in re.finditer(r"\{[^}]*\}", cleaned):
            classes.extend(self._extract_object_keys(obj_match.group(0)))
        return classes

    @staticmethod
    def _extract_from_template_literal(value: str) -> list[str]:
        """
        Helper to extract classes from a template literal body.
        """
        classes = TailwindUsageProcessor._extract_template_literal_values(value)
        for expr in re.findall(r"\$\{([^}]*)\}", value):
            classes.extend(TailwindUsageProcessor._extract_string_literals(expr))
        return classes

    @staticmethod
    def _extract_object_keys(value: str) -> list[str]:
        """
        Extract keys from a JavaScript object string (used in classnames/clsx).
        """
        keys = re.findall(r"['\"]([^'\"]+)['\"]\s*:", value)
        keys.extend(re.findall(r"\b([A-Za-z0-9_-]+)\s*:", value))
        return keys

    @staticmethod
    def _normalize_utility(value: str) -> str:
        """
        Normalize a utility class name (trim, remove invalid chars).
        """
        cleaned = value.strip().strip(";")
        cleaned = re.sub(r"\s*!important$", "", cleaned)
        if not cleaned:
            return ""
        if "{{" in cleaned or "}}" in cleaned:
            return ""
        if cleaned.startswith("{") or cleaned.endswith("}"):
            return ""
        return cleaned

    @staticmethod
    def _slice_text(source_text: str, start: int, end: int) -> str:
        """
        Safely slice source text using byte offsets.
        """
        return source_text[start:end]

    def _get_query(self, language: cs.SupportedLanguage, name: str) -> Query | None:
        """
        Get a compiled SCM query for the language.
        """
        cache_key = (language, name)
        if cache_key in self._compiled_queries:
            return self._compiled_queries[cache_key]

        lang_queries = self.queries.get(language)
        if not lang_queries:
            return None
        language_obj = lang_queries.get("language")
        if language_obj is None:
            return None

        scm_file = self._get_scm_file(language)
        if not scm_file.exists():
            return None

        scm_text = scm_file.read_text(encoding="utf-8", errors="ignore")
        scm_queries = _parse_scm_queries(scm_text)
        query_text = scm_queries.get(name)
        if not query_text:
            return None

        try:
            compiled = Query(language_obj, query_text)
        except Exception:
            return None

        self._compiled_queries[cache_key] = compiled
        return compiled

    def _get_scm_file(self, language: cs.SupportedLanguage) -> Path:
        """
        Get the path to the SCM file for a language (mapped alias).
        """
        lang_name = _SCM_LANGUAGE_ALIAS.get(language, language.value)
        return Path(__file__).parent / "queries" / f"{lang_name}.scm"

    def _module_qn_for_path(self, file_path: Path) -> str:
        """
        Get the qualified name for the module at the given path.
        """
        relative_path = file_path.relative_to(self.repo_path)
        parts = list(relative_path.with_suffix("").parts)
        if file_path.name in (cs.INIT_PY, cs.MOD_RS):
            parts = list(relative_path.parent.parts)
        return cs.SEPARATOR_DOT.join([self.project_name, *parts])

    def _ensure_tailwind_asset(self) -> None:
        """
        Ensure the Tailwind CSS asset node exists in the graph.
        """
        if self._tailwind_asset_qn:
            return
        asset_qn = (
            f"{self.project_name}{cs.SEPARATOR_DOT}asset.css_framework.tailwindcss"
        )
        self.ingestor.ensure_node_batch(
            cs.NodeLabel.ASSET,
            {
                cs.KEY_QUALIFIED_NAME: asset_qn,
                cs.KEY_NAME: "tailwindcss",
                cs.KEY_ASSET_HANDLE: "tailwindcss",
                cs.KEY_ASSET_TYPE: "css_framework",
            },
        )
        self._tailwind_asset_qn = asset_qn

    def _ensure_tailwind_config_asset(
        self,
        config_paths: list[str],
        content: list[str],
        safelist: list[str],
        source_inline: list[str],
    ) -> None:
        """
        Create the Tailwind configuration asset node.
        """
        asset_qn = f"{self.project_name}{cs.SEPARATOR_DOT}asset.tailwind.config"
        props = {
            cs.KEY_QUALIFIED_NAME: asset_qn,
            cs.KEY_NAME: "tailwind.config",
            cs.KEY_ASSET_HANDLE: "tailwind.config",
            cs.KEY_ASSET_TYPE: "tailwind_config",
        }
        if config_paths:
            props[cs.KEY_ASSET_PATH] = json.dumps(config_paths, ensure_ascii=False)
        if content:
            props[cs.KEY_TAILWIND_CONTENT] = json.dumps(content, ensure_ascii=False)
        if safelist:
            props[cs.KEY_TAILWIND_SAFELIST] = json.dumps(safelist, ensure_ascii=False)
        if source_inline:
            props[cs.KEY_TAILWIND_SOURCE_INLINE] = json.dumps(
                source_inline, ensure_ascii=False
            )

        self.ingestor.ensure_node_batch(cs.NodeLabel.ASSET, props)
        self.ingestor.ensure_relationship_batch(
            (cs.NodeLabel.PROJECT, cs.KEY_NAME, self.project_name),
            cs.RelationshipType.USES_ASSET,
            (cs.NodeLabel.ASSET, cs.KEY_QUALIFIED_NAME, asset_qn),
            {cs.KEY_RELATION_TYPE: "tailwind_config"},
        )
        self._tailwind_config_qn = asset_qn

    def _ensure_utility_node(self, utility: str) -> str:
        """
        Ensure a node exists for a specific Tailwind utility class.
        """
        normalized = re.sub(r"[^A-Za-z0-9_]+", "_", utility).strip("_")
        if not normalized:
            normalized = hashlib.md5(
                utility.encode("utf-8"), usedforsecurity=False
            ).hexdigest()[:8]
        utility_qn = (
            f"{self.project_name}{cs.SEPARATOR_DOT}tailwind.utility.{normalized}"
        )
        self.ingestor.ensure_node_batch(
            cs.NodeLabel.TAILWIND_UTILITY,
            {
                cs.KEY_QUALIFIED_NAME: utility_qn,
                cs.KEY_NAME: utility,
                cs.KEY_UTILITY_NAME: utility,
            },
        )
        return utility_qn
