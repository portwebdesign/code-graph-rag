from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from loguru import logger
from tree_sitter import Language, Parser, Query

from codebase_rag.core import constants as cs
from codebase_rag.data_models.types_defs import LanguageQueries

_QUERY_NAME_MAP: dict[cs.SupportedLanguage, dict[str, str | list[str]]] = {
    cs.SupportedLanguage.PYTHON: {
        "functions": [
            "function_definition",
            "async_function_definition",
            "method_definition",
        ],
        "classes": "class_definition",
        "calls": ["call_edge", "method_call_edge", "chained_call_edge"],
        "imports": "import_edge",
    },
    cs.SupportedLanguage.JS: {
        "functions": [
            "function_definition",
            "async_function_definition",
            "generator_definition",
            "function_expression_definition",
            "arrow_function_definition",
            "method_definition",
        ],
        "classes": ["class_definition", "class_expression"],
        "calls": ["call_edge", "method_call_edge", "constructor_call_edge"],
        "imports": ["import_edge", "named_import_edge", "export_edge"],
    },
    cs.SupportedLanguage.TS: {
        "functions": [
            "function_declaration",
            "async_function",
            "generator_function",
            "arrow_function",
            "method_definition",
        ],
        "classes": [
            "class_definition",
            "interface_definition",
            "enum_definition",
            "type_alias",
        ],
        "calls": ["call_expression", "method_call", "constructor_call"],
        "imports": ["import_statement", "named_import", "export_statement"],
    },
    cs.SupportedLanguage.JAVA: {
        "functions": [
            "method_declarations",
            "constructor_declarations",
            "lambda_expressions",
        ],
        "classes": [
            "class_declarations",
            "interface_declarations",
            "enum_declarations",
            "record_declarations",
            "annotation_type_declarations",
        ],
        "calls": [
            "method_invocations",
            "object_creation",
            "method_references",
        ],
        "imports": ["import_statements", "static_imports", "package_declarations"],
    },
    cs.SupportedLanguage.RUST: {
        "functions": [
            "function_definitions",
            "method_definitions",
            "async_functions",
        ],
        "classes": [
            "struct_declarations",
            "enum_declarations",
            "union_declarations",
            "trait_declarations",
            "impl_blocks",
            "macro_definitions",
        ],
        "calls": ["call_expressions", "method_calls", "macro_invocations"],
        "imports": "use_statements",
    },
    cs.SupportedLanguage.CPP: {
        "functions": [
            "function_declarations",
            "function_definitions",
            "constructor_declarations",
            "destructor_declarations",
            "lambda_expressions",
        ],
        "classes": [
            "class_definitions",
            "struct_definitions",
            "union_definitions",
            "enum_definitions",
        ],
        "calls": ["function_calls", "method_invocations"],
        "imports": ["include_directives", "module_imports"],
    },
    cs.SupportedLanguage.CSHARP: {
        "functions": [
            "method",
            "constructor",
            "local_function",
            "lambda",
            "anonymous_method",
        ],
        "classes": ["class", "struct", "interface", "record"],
        "calls": ["call", "member_call", "linq_call", "minimal_api_call"],
        "imports": "import",
    },
    cs.SupportedLanguage.GO: {
        "functions": "function_definitions",
        "classes": "class_definitions",
        "calls": "function_calls",
        "imports": "import_statements",
    },
    cs.SupportedLanguage.SCALA: {
        "functions": ["function_definitions", "anonymous_functions"],
        "classes": ["class_definitions", "case_class", "companion_object"],
        "calls": ["call_expressions", "infix_calls", "apply_calls"],
        "imports": "import_statements",
    },
    cs.SupportedLanguage.RUBY: {
        "functions": ["instance_methods", "class_methods"],
        "classes": ["class_definitions", "module_definitions"],
        "calls": [
            "associations",
            "validations",
            "scopes",
            "callbacks",
            "routes",
            "migrations",
            "define_method_usage",
            "dynamic_send",
        ],
        "imports": "require_statements",
    },
    cs.SupportedLanguage.KOTLIN: {
        "functions": [
            "function_declarations",
            "extension_functions",
            "lambda_expressions",
        ],
        "classes": [
            "class_declarations",
            "data_class_declarations",
            "interface_declarations",
            "enum_declarations",
            "sealed_class_declarations",
        ],
        "calls": "coroutine_calls",
        "imports": "imports",
    },
    cs.SupportedLanguage.PHP: {
        "functions": [
            "function_definitions",
            "method_definitions",
            "closure_definitions",
            "arrow_functions",
        ],
        "classes": [
            "class_definitions",
            "interface_definitions",
            "trait_definitions",
        ],
        "calls": ["function_calls", "member_calls", "static_method_calls"],
        "imports": ["include_statements", "namespaces", "use_statements"],
    },
    cs.SupportedLanguage.HTML: {
        "functions": "script_blocks",
        "classes": "elements",
        "calls": "style_blocks",
        "imports": ["external_scripts", "css_links"],
    },
    cs.SupportedLanguage.CSS: {
        "functions": "function_definitions",
        "classes": "class_definitions",
        "calls": "function_calls",
        "imports": "import_statements",
    },
    cs.SupportedLanguage.SCSS: {
        "functions": "function_definition",
        "classes": "class_selector",
        "calls": "function_call",
        "imports": "import_statements",
    },
    cs.SupportedLanguage.GRAPHQL: {
        "functions": "function_definitions",
        "classes": "class_definitions",
        "calls": "function_calls",
        "imports": "import_statements",
    },
    cs.SupportedLanguage.DOCKERFILE: {
        "functions": "function_definitions",
        "classes": "copy_instruction",
        "calls": [
            "function_calls",
            "node_package_install",
            "python_package_install",
            "go_package_install",
            "rust_package_install",
            "system_package_install",
        ],
        "imports": [
            "copy_instruction",
            "copy_package_json",
            "copy_requirements",
            "copy_go_mod",
            "copy_cargo_toml",
            "copy_pyproject",
        ],
    },
    cs.SupportedLanguage.SQL: {
        "functions": ["function_definition", "procedure_definition"],
        "classes": [
            "table_definition",
            "view_definition",
            "materialized_view_definition",
        ],
        "calls": [
            "select_statement",
            "insert_statement",
            "update_statement",
            "delete_statement",
        ],
        "imports": ["from_clause", "join_clause"],
    },
    cs.SupportedLanguage.VUE: {
        "functions": "script_root",
        "classes": "element_definition",
        "calls": [
            "v_if_directive",
            "v_for_directive",
            "v_model_directive",
            "event_handler",
        ],
        "imports": "import_statements",
    },
    cs.SupportedLanguage.SVELTE: {
        "functions": "script_root",
        "classes": "element_definition",
        "calls": "expression_tag",
        "imports": "import_script",
    },
    cs.SupportedLanguage.YAML: {
        "functions": "yaml_document",
        "classes": "mapping_pair",
        "calls": ["block_sequence", "flow_sequence"],
        "imports": "parent_child_key_edge",
    },
    cs.SupportedLanguage.JSON: {
        "functions": "json_document",
        "classes": "json_object",
        "calls": "json_array",
        "imports": "key_value_pair",
    },
}

_SCM_LANGUAGE_ALIAS: dict[cs.SupportedLanguage, str] = {
    cs.SupportedLanguage.CSHARP: "csharp",
}

_CAPTURE_ALIAS_MAP: dict[str, tuple[str, ...]] = {
    "functions": (
        "function_definition",
        "async_function_definition",
        "generator_definition",
        "function_expression_definition",
        "arrow_function_definition",
        "method_definition",
        "function_declaration",
        "async_function",
        "generator_function",
        "arrow_function",
        "function_definitions",
        "method_definitions",
        "async_functions",
        "function_declarations",
        "constructor_declarations",
        "destructor_declarations",
        "lambda_expressions",
        "method",
        "constructor",
        "local_function",
        "lambda",
        "anonymous_method",
        "instance_method_node",
        "class_method_node",
        "function_node",
        "method_node",
    ),
    "classes": (
        "class_definition",
        "class_expression",
        "interface_definition",
        "enum_definition",
        "type_alias",
        "class_declarations",
        "interface_declarations",
        "enum_declarations",
        "record_declarations",
        "annotation_type_declarations",
        "struct_declarations",
        "union_declarations",
        "trait_declarations",
        "impl_blocks",
        "macro_definitions",
        "class_definitions",
        "struct_definitions",
        "union_definitions",
        "enum_definitions",
        "class",
        "struct",
        "interface",
        "record",
        "class_node",
        "interface_node",
        "enum_node",
        "type_alias_node",
        "module_definitions",
        "module_node",
        "element_definition",
    ),
    "calls": (
        "call_edge",
        "method_call_edge",
        "constructor_call_edge",
        "call_expression",
        "method_call",
        "constructor_call",
        "method_invocations",
        "object_creation",
        "method_references",
        "call_expressions",
        "method_calls",
        "macro_invocations",
        "function_calls",
        "call",
        "member_call",
        "linq_call",
        "minimal_api_call",
        "infix_calls",
        "apply_calls",
        "coroutine_calls",
        "select_statement",
        "insert_statement",
        "update_statement",
        "delete_statement",
        "v_if_directive",
        "v_for_directive",
        "v_model_directive",
        "event_handler",
    ),
    "imports": (
        "import_edge",
        "named_import_edge",
        "export_edge",
        "import_statement",
        "named_import",
        "export_statement",
        "import_statements",
        "use_statements",
        "include_directives",
        "module_imports",
        "import",
        "require_statements",
        "imports",
        "external_scripts",
        "css_links",
        "import_script",
        "parent_child_key_edge",
        "key_value_pair",
        "from_clause",
        "join_clause",
        "include_statements",
        "namespaces",
        "copy_instruction",
        "copy_package_json",
        "copy_requirements",
        "copy_go_mod",
        "copy_cargo_toml",
        "copy_pyproject",
    ),
}


def _parse_scm_queries(scm_text: str) -> dict[str, str]:
    """
    Parses SCM text to extract named queries.

    Args:
        scm_text (str): The content of the .scm file.

    Returns:
        dict[str, str]: A dictionary mapping query names to query strings.
    """
    queries: dict[str, str] = {}
    query_pattern = (
        r"(?:^|\n)\s*;?\s*@query:\s*(\w+)\s*\n" r"((?:(?!\n\s*;?\s*@query:)[\s\S])*)"
    )

    import re

    for match in re.finditer(query_pattern, scm_text):
        query_name = match.group(1).strip()
        query_string = match.group(2).strip()
        if query_string:
            queries[query_name] = query_string

    return queries


def _combine_queries(query_strings: Iterable[str]) -> str:
    """
    Combines multiple query strings into a single string.

    Args:
        query_strings (Iterable[str]): An iterable of query strings.

    Returns:
        str: The combined query string.
    """
    return "\n\n".join(q for q in query_strings if q)


def _normalize_query_captures(
    query_strings: Iterable[str],
    standard_capture: str,
    aliases: Iterable[str],
) -> str:
    normalized_parts: list[str] = []
    for query_text in query_strings:
        if not query_text:
            continue
        normalized = query_text
        for alias in aliases:
            normalized = normalized.replace(f"@{alias}", f"@{standard_capture}")
        normalized_parts.append(normalized)

    combined = _combine_queries(normalized_parts)
    if f"@{standard_capture}" not in combined:
        return ""
    return combined


def _compile_query(
    language: Language, query_text: str, *, log_warning: bool = True
) -> Query | None:
    """
    Compiles a tree-sitter query string.

    Args:
        language (Language): The Tree-sitter language object.
        query_text (str): The query string.

    Returns:
        Query | None: The compiled Query object, or None if compilation fails.
    """
    if not query_text.strip():
        return None
    try:
        return Query(language, query_text)
    except Exception as e:
        if log_warning:
            logger.warning(f"Failed to compile SCM query: {e}")
        return None


def _get_scm_file(language: cs.SupportedLanguage) -> Path:
    """
    Gets the path to the .scm file for a given language.

    Args:
        language (cs.SupportedLanguage): The supported language.

    Returns:
        Path: The path to the .scm file.
    """
    lang_name = _SCM_LANGUAGE_ALIAS.get(language, language.value)
    return Path(__file__).parent / "queries" / f"{lang_name}.scm"


def _apply_language_override(
    language: cs.SupportedLanguage,
    parser: Parser,
    base_queries: LanguageQueries,
) -> LanguageQueries | None:
    """
    Applies SCM query overrides for a specific language.

    Args:
        language (cs.SupportedLanguage): The language to override.
        parser (Parser): The Tree-sitter parser.
        base_queries (LanguageQueries): The original queries.

    Returns:
        LanguageQueries | None: The updated queries if overrides exist, else None.
    """
    if language not in _QUERY_NAME_MAP:
        return None

    scm_file = _get_scm_file(language)
    if not scm_file.exists():
        return None

    scm_text = scm_file.read_text(encoding="utf-8", errors="ignore")
    scm_queries = _parse_scm_queries(scm_text)

    language_obj: Language = base_queries["language"]

    overrides: dict[str, Query | None] = {}
    query_names = _QUERY_NAME_MAP[language]

    standard_captures = {
        "functions": cs.CAPTURE_FUNCTION,
        "classes": cs.CAPTURE_CLASS,
        "calls": cs.CAPTURE_CALL,
        "imports": cs.CAPTURE_IMPORT,
    }

    for key in ("functions", "classes", "calls", "imports"):
        name_or_names = query_names.get(key)
        if not name_or_names:
            continue

        alias_list: list[str] = list(_CAPTURE_ALIAS_MAP.get(key, ()))
        if isinstance(name_or_names, list):
            alias_list.extend(name_or_names)
            parts = [scm_queries.get(name, "") for name in name_or_names]
        else:
            alias_list.append(name_or_names)
            parts = [scm_queries.get(name_or_names, "")]

        normalized = _normalize_query_captures(
            parts,
            standard_captures[key],
            dict.fromkeys(alias_list),
        )
        if not normalized:
            overrides[key] = None
            continue

        valid_parts: list[str] = []
        invalid_parts = 0
        for part in parts:
            normalized_part = _normalize_query_captures(
                [part],
                standard_captures[key],
                dict.fromkeys(alias_list),
            )
            if not normalized_part:
                continue
            if _compile_query(language_obj, normalized_part, log_warning=False):
                valid_parts.append(normalized_part)
            else:
                invalid_parts += 1

        if invalid_parts:
            logger.debug(
                "Skipped {} invalid SCM query fragments for {}:{}",
                invalid_parts,
                language.value,
                key,
            )

        normalized_valid = _combine_queries(valid_parts)
        overrides[key] = (
            _compile_query(language_obj, normalized_valid) if normalized_valid else None
        )

    merged: LanguageQueries = {
        "functions": overrides.get("functions") or base_queries["functions"],
        "classes": overrides.get("classes") or base_queries["classes"],
        "calls": overrides.get("calls") or base_queries["calls"],
        "imports": overrides.get("imports") or base_queries["imports"],
        "locals": base_queries["locals"],
        "config": base_queries["config"],
        "language": base_queries["language"],
        "parser": parser,
    }

    logger.info(f"Applied SCM query overrides for {language.value}")
    return merged


def apply_scm_query_overrides(
    parsers: dict[cs.SupportedLanguage, Parser],
    queries: dict[cs.SupportedLanguage, LanguageQueries],
) -> dict[cs.SupportedLanguage, LanguageQueries]:
    """
    Applies SCM query overrides to the provided parsers and queries.

    This function iterates through supported languages and attempts to load
    query definitions from .scm files, overriding the default hardcoded queries.

    Args:
        parsers (dict[cs.SupportedLanguage, Parser]): Dictionary of parsers.
        queries (dict[cs.SupportedLanguage, LanguageQueries]): Dictionary of original queries.

    Returns:
        dict[cs.SupportedLanguage, LanguageQueries]: A new dictionary with updated queries.
    """
    updated: dict[cs.SupportedLanguage, LanguageQueries] = dict(queries)

    for language, base_queries in queries.items():
        parser = parsers.get(language)
        if not parser:
            continue

        override = _apply_language_override(language, parser, base_queries)
        if override:
            updated[language] = override

    return updated
