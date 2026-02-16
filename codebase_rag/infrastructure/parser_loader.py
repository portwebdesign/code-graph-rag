import importlib
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

from loguru import logger
from tree_sitter import Language, Parser, Query

from codebase_rag.core import constants as cs
from codebase_rag.core import logs as ls
from codebase_rag.data_models.types_defs import (
    LanguageImport,
    LanguageLoader,
    LanguageQueries,
)
from codebase_rag.parsers.query.query_engine_adapter import apply_scm_query_overrides

from . import exceptions as ex
from .language_spec import LANGUAGE_SPECS, LanguageSpec


def _try_load_from_submodule(lang_name: cs.SupportedLanguage) -> LanguageLoader:
    """Attempts to load a tree-sitter language grammar from a git submodule.

    This function is a fallback for when a language grammar is not installed as a
    standard Python package. It looks for the grammar in the `grammars/` directory,
    tries to build the C bindings if necessary, and then loads it.

    Args:
        lang_name (cs.SupportedLanguage): The name of the language to load.

    Returns:
        LanguageLoader: A loader function for the language, or None if it fails.
    """
    submodule_path = Path(cs.GRAMMARS_DIR) / f"{cs.TREE_SITTER_PREFIX}{lang_name}"
    python_bindings_path = (
        submodule_path / cs.BINDINGS_DIR / cs.SupportedLanguage.PYTHON
    )

    if not python_bindings_path.exists():
        return None

    python_bindings_str = str(python_bindings_path)
    try:
        if python_bindings_str not in sys.path:
            sys.path.insert(0, python_bindings_str)

        try:
            module_name = f"{cs.TREE_SITTER_MODULE_PREFIX}{lang_name.replace('-', '_')}"
            language_attrs: list[str] = [
                cs.QUERY_LANGUAGE,
                f"{cs.LANG_ATTR_PREFIX}{lang_name}",
                f"{cs.LANG_ATTR_PREFIX}{lang_name.replace('-', '_')}",
            ]

            def _load_from_module() -> LanguageLoader | None:
                logger.debug(ls.IMPORTING_MODULE.format(module=module_name))
                sys.modules.pop(module_name, None)
                module = importlib.import_module(module_name)

                for attr_name in language_attrs:
                    if hasattr(module, attr_name):
                        logger.debug(
                            ls.LOADED_FROM_SUBMODULE.format(
                                lang=lang_name, attr=attr_name
                            )
                        )
                        loader: LanguageLoader = getattr(module, attr_name)
                        return loader

                logger.debug(
                    ls.NO_LANG_ATTR.format(module=module_name, available=dir(module))
                )
                return None

            existing_loader = _load_from_module()
            if existing_loader is not None:
                return existing_loader

            setup_py_path = submodule_path / cs.SETUP_PY
            if setup_py_path.exists():
                logger.debug(ls.BUILDING_BINDINGS.format(lang=lang_name))
                result = subprocess.run(
                    [sys.executable, cs.SETUP_PY, cs.BUILD_EXT_CMD, cs.INPLACE_FLAG],
                    check=False,
                    cwd=str(submodule_path),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )

                if result.returncode != 0:
                    logger.debug(
                        ls.BUILD_FAILED.format(
                            lang=lang_name, stdout=result.stdout, stderr=result.stderr
                        )
                    )
                    return None
                logger.debug(ls.BUILD_SUCCESS.format(lang=lang_name))

            rebuilt_loader = _load_from_module()
            if rebuilt_loader is not None:
                return rebuilt_loader

        finally:
            if python_bindings_str in sys.path:
                sys.path.remove(python_bindings_str)

    except Exception as e:
        logger.debug(ls.SUBMODULE_LOAD_FAILED.format(lang=lang_name, error=e))

    return None


def _try_import_language(
    module_path: str, attr_name: str, lang_name: cs.SupportedLanguage
) -> LanguageLoader:
    """Tries to import a language from a standard package, falling back to submodule loading.

    Args:
        module_path (str): The Python module path (e.g., 'tree_sitter_python').
        attr_name (str): The attribute name for the language loader function.
        lang_name (cs.SupportedLanguage): The language name for the submodule fallback.

    Returns:
        LanguageLoader: The language loader function, or None if it fails.
    """
    try:
        module = importlib.import_module(module_path)
        loader: LanguageLoader = getattr(module, attr_name)
        return loader
    except (ImportError, AttributeError):
        return _try_load_from_submodule(lang_name)


def _import_language_loaders() -> dict[cs.SupportedLanguage, LanguageLoader]:
    """Imports all configured language loaders.

    It iterates through a predefined list of languages and attempts to load each one.

    Returns:
        dict[cs.SupportedLanguage, LanguageLoader]: A dictionary mapping language names
            to their loader functions.
    """
    language_imports: list[LanguageImport] = [
        LanguageImport(
            cs.SupportedLanguage.PYTHON,
            cs.TreeSitterModule.PYTHON,
            cs.QUERY_LANGUAGE,
            cs.SupportedLanguage.PYTHON,
        ),
        LanguageImport(
            cs.SupportedLanguage.JS,
            cs.TreeSitterModule.JS,
            cs.QUERY_LANGUAGE,
            cs.SupportedLanguage.JS,
        ),
        LanguageImport(
            cs.SupportedLanguage.TS,
            cs.TreeSitterModule.TS,
            cs.LANG_ATTR_TYPESCRIPT,
            cs.SupportedLanguage.TS,
        ),
        LanguageImport(
            cs.SupportedLanguage.RUST,
            cs.TreeSitterModule.RUST,
            cs.QUERY_LANGUAGE,
            cs.SupportedLanguage.RUST,
        ),
        LanguageImport(
            cs.SupportedLanguage.GO,
            cs.TreeSitterModule.GO,
            cs.QUERY_LANGUAGE,
            cs.SupportedLanguage.GO,
        ),
        LanguageImport(
            cs.SupportedLanguage.SCALA,
            cs.TreeSitterModule.SCALA,
            cs.QUERY_LANGUAGE,
            cs.SupportedLanguage.SCALA,
        ),
        LanguageImport(
            cs.SupportedLanguage.JAVA,
            cs.TreeSitterModule.JAVA,
            cs.QUERY_LANGUAGE,
            cs.SupportedLanguage.JAVA,
        ),
        LanguageImport(
            cs.SupportedLanguage.CPP,
            cs.TreeSitterModule.CPP,
            cs.QUERY_LANGUAGE,
            cs.SupportedLanguage.CPP,
        ),
        LanguageImport(
            cs.SupportedLanguage.CSHARP,
            cs.TreeSitterModule.CSHARP,
            cs.QUERY_LANGUAGE,
            cs.SupportedLanguage.CSHARP,
        ),
        LanguageImport(
            cs.SupportedLanguage.LUA,
            cs.TreeSitterModule.LUA,
            cs.QUERY_LANGUAGE,
            cs.SupportedLanguage.LUA,
        ),
        LanguageImport(
            cs.SupportedLanguage.RUBY,
            cs.TreeSitterModule.RUBY,
            cs.QUERY_LANGUAGE,
            cs.SupportedLanguage.RUBY,
        ),
        LanguageImport(
            cs.SupportedLanguage.KOTLIN,
            cs.TreeSitterModule.KOTLIN,
            cs.QUERY_LANGUAGE,
            cs.SupportedLanguage.KOTLIN,
        ),
        LanguageImport(
            cs.SupportedLanguage.YAML,
            cs.TreeSitterModule.YAML,
            cs.QUERY_LANGUAGE,
            cs.SupportedLanguage.YAML,
        ),
        LanguageImport(
            cs.SupportedLanguage.JSON,
            cs.TreeSitterModule.JSON,
            cs.QUERY_LANGUAGE,
            cs.SupportedLanguage.JSON,
        ),
        LanguageImport(
            cs.SupportedLanguage.PHP,
            cs.TreeSitterModule.PHP,
            f"{cs.LANG_ATTR_PREFIX}{cs.SupportedLanguage.PHP}",
            cs.SupportedLanguage.PHP,
        ),
        LanguageImport(
            cs.SupportedLanguage.HTML,
            cs.TreeSitterModule.HTML,
            cs.QUERY_LANGUAGE,
            cs.SupportedLanguage.HTML,
        ),
        LanguageImport(
            cs.SupportedLanguage.CSS,
            cs.TreeSitterModule.CSS,
            cs.QUERY_LANGUAGE,
            cs.SupportedLanguage.CSS,
        ),
        LanguageImport(
            cs.SupportedLanguage.SCSS,
            cs.TreeSitterModule.SCSS,
            cs.QUERY_LANGUAGE,
            cs.SupportedLanguage.SCSS,
        ),
        LanguageImport(
            cs.SupportedLanguage.GRAPHQL,
            cs.TreeSitterModule.GRAPHQL,
            cs.QUERY_LANGUAGE,
            cs.SupportedLanguage.GRAPHQL,
        ),
        LanguageImport(
            cs.SupportedLanguage.DOCKERFILE,
            cs.TreeSitterModule.DOCKERFILE,
            cs.QUERY_LANGUAGE,
            cs.SupportedLanguage.DOCKERFILE,
        ),
        LanguageImport(
            cs.SupportedLanguage.SQL,
            cs.TreeSitterModule.SQL,
            cs.QUERY_LANGUAGE,
            cs.SupportedLanguage.SQL,
        ),
        LanguageImport(
            cs.SupportedLanguage.VUE,
            cs.TreeSitterModule.VUE,
            cs.QUERY_LANGUAGE,
            cs.SupportedLanguage.VUE,
        ),
        LanguageImport(
            cs.SupportedLanguage.SVELTE,
            cs.TreeSitterModule.SVELTE,
            cs.QUERY_LANGUAGE,
            cs.SupportedLanguage.SVELTE,
        ),
    ]

    loaders: dict[cs.SupportedLanguage, LanguageLoader] = {
        lang_import.lang_key: _try_import_language(
            lang_import.module_path,
            lang_import.attr_name,
            lang_import.submodule_name,
        )
        for lang_import in language_imports
    }
    for lang_key in LANGUAGE_SPECS:
        lang_name = cs.SupportedLanguage(lang_key)
        if lang_name not in loaders or loaders[lang_name] is None:
            loaders[lang_name] = _try_load_from_submodule(lang_name)

    return loaders


_language_loaders = _import_language_loaders()

LANGUAGE_LIBRARIES: dict[cs.SupportedLanguage, LanguageLoader] = _language_loaders


def _build_query_pattern(node_types: tuple[str, ...], capture_name: str) -> str:
    """Builds a tree-sitter query pattern from a list of node types.

    Example: `(function_definition) @function (class_definition) @class`

    Args:
        node_types (tuple[str, ...]): The node types to capture.
        capture_name (str): The name to assign to the capture (e.g., '@function').

    Returns:
        str: The combined query pattern string.
    """
    return " ".join([f"({node_type}) @{capture_name}" for node_type in node_types])


def _get_locals_pattern(lang_name: cs.SupportedLanguage) -> str | None:
    """Gets the language-specific pattern for capturing local definitions.

    Args:
        lang_name (cs.SupportedLanguage): The language to get the pattern for.

    Returns:
        str | None: The locals query pattern, or None if not defined for the language.
    """
    match lang_name:
        case cs.SupportedLanguage.JS:
            return cs.JS_LOCALS_PATTERN
        case cs.SupportedLanguage.TS:
            return cs.TS_LOCALS_PATTERN
        case _:
            return None


def _build_combined_import_pattern(lang_config: LanguageSpec) -> str:
    """Builds a combined query pattern for both `import` and `from ... import` statements.

    Args:
        lang_config (LanguageSpec): The language specification containing import node types.

    Returns:
        str: The combined query pattern string for imports.
    """
    import_patterns = _build_query_pattern(
        lang_config.import_node_types, cs.CAPTURE_IMPORT
    )
    import_from_patterns = _build_query_pattern(
        lang_config.import_from_node_types, cs.CAPTURE_IMPORT_FROM
    )

    all_patterns: list[str] = []
    if import_patterns.strip():
        all_patterns.append(import_patterns)
    if import_from_patterns.strip() and import_from_patterns != import_patterns:
        all_patterns.append(import_from_patterns)
    return " ".join(all_patterns)


def _create_optional_query(language: Language, pattern: str | None) -> Query | None:
    """Creates a tree-sitter Query object if a pattern is provided.

    Args:
        language (Language): The tree-sitter Language object.
        pattern (str | None): The query pattern string.

    Returns:
        Query | None: A Query object, or None if the pattern is empty.
    """
    if not pattern or not pattern.strip():
        return None
    return Query(language, pattern)


def _create_locals_query(
    language: Language, lang_name: cs.SupportedLanguage
) -> Query | None:
    """Creates a tree-sitter Query for capturing local definitions.

    Args:
        language (Language): The tree-sitter Language object.
        lang_name (cs.SupportedLanguage): The name of the language.

    Returns:
        Query | None: A Query object for locals, or None if no pattern is defined.
    """
    locals_pattern = _get_locals_pattern(lang_name)
    if not locals_pattern:
        return None
    try:
        return Query(language, locals_pattern)
    except Exception as e:
        logger.debug(ls.LOCALS_QUERY_FAILED.format(lang=lang_name, error=e))
        return None


def _create_language_queries(
    language: Language,
    parser: Parser,
    lang_config: LanguageSpec,
    lang_name: cs.SupportedLanguage,
) -> LanguageQueries:
    """Creates a full set of queries for a given language.

    Args:
        language (Language): The tree-sitter Language object.
        parser (Parser): The tree-sitter Parser object.
        lang_config (LanguageSpec): The configuration for the language.
        lang_name (cs.SupportedLanguage): The name of the language.

    Returns:
        LanguageQueries: A dataclass containing all queries for the language.
    """
    function_patterns = lang_config.function_query or _build_query_pattern(
        lang_config.function_node_types, cs.CAPTURE_FUNCTION
    )
    class_patterns = lang_config.class_query or _build_query_pattern(
        lang_config.class_node_types, cs.CAPTURE_CLASS
    )
    call_patterns = lang_config.call_query or _build_query_pattern(
        lang_config.call_node_types, cs.CAPTURE_CALL
    )
    combined_import_patterns = _build_combined_import_pattern(lang_config)

    return LanguageQueries(
        functions=_create_optional_query(language, function_patterns),
        classes=_create_optional_query(language, class_patterns),
        calls=_create_optional_query(language, call_patterns),
        imports=_create_optional_query(language, combined_import_patterns),
        locals=_create_locals_query(language, lang_name),
        config=lang_config,
        language=language,
        parser=parser,
    )


def _process_language(
    lang_name: cs.SupportedLanguage,
    lang_config: LanguageSpec,
    parsers: dict[cs.SupportedLanguage, Parser],
    queries: dict[cs.SupportedLanguage, LanguageQueries],
) -> bool:
    """Processes a single language: loads its grammar, creates a parser, and builds queries.

    Args:
        lang_name (cs.SupportedLanguage): The language to process.
        lang_config (LanguageSpec): The configuration for the language.
        parsers (dict): The dictionary to store the created parser in.
        queries (dict): The dictionary to store the created queries in.

    Returns:
        bool: True if the language was processed successfully, False otherwise.
    """
    if _is_windows_unsupported(lang_name):
        logger.warning(
            f"Skipping {lang_name} parser on Windows (enable CODEGRAPH_WINDOWS_ALLOW_UNSUPPORTED=1 to force)."
        )
        return False

    lang_lib = LANGUAGE_LIBRARIES.get(lang_name)
    if not lang_lib:
        logger.debug(ls.LIB_NOT_AVAILABLE.format(lang=lang_name))
        return False

    try:
        lang_obj = lang_lib()
        if isinstance(lang_obj, Language):
            language = lang_obj
        elif isinstance(lang_obj, int):
            language = Language(lang_obj)
        else:
            language = Language(lang_obj)
        parser = Parser(language)
        parsers[lang_name] = parser
        queries[lang_name] = _create_language_queries(
            language, parser, lang_config, lang_name
        )
        logger.success(ls.GRAMMAR_LOADED.format(lang=lang_name))
        return True
    except Exception as e:
        logger.warning(ls.GRAMMAR_LOAD_FAILED.format(lang=lang_name, error=e))
        return False


def _is_windows_unsupported(lang_name: cs.SupportedLanguage) -> bool:
    if os.name != "nt":
        return False

    allow_unsupported = os.getenv(
        "CODEGRAPH_WINDOWS_ALLOW_UNSUPPORTED", ""
    ).lower() in {"1", "true", "yes"}

    if allow_unsupported:
        return False

    return lang_name in {
        cs.SupportedLanguage.VUE,
        cs.SupportedLanguage.KOTLIN,
        cs.SupportedLanguage.SCSS,
    }


def load_parsers() -> tuple[
    dict[cs.SupportedLanguage, Parser], dict[cs.SupportedLanguage, LanguageQueries]
]:
    """Loads all available tree-sitter parsers and queries.

    This is the main entry point of the module. It iterates through all languages
    defined in `LANGUAGE_SPECS`, attempts to load them, and returns the successfully
    loaded parsers and queries.

    Raises:
        RuntimeError: If no languages could be loaded at all.

    Returns:
        tuple: A tuple containing two dictionaries:
            - A mapping of language names to `Parser` objects.
            - A mapping of language names to `LanguageQueries` objects.
    """
    parsers: dict[cs.SupportedLanguage, Parser] = {}
    queries: dict[cs.SupportedLanguage, LanguageQueries] = {}
    available_languages: list[cs.SupportedLanguage] = []

    for lang_key, lang_config in deepcopy(LANGUAGE_SPECS).items():
        lang_name = cs.SupportedLanguage(lang_key)
        if _process_language(lang_name, lang_config, parsers, queries):
            available_languages.append(lang_name)

    if not available_languages:
        raise RuntimeError(ex.NO_LANGUAGES)

    queries = apply_scm_query_overrides(parsers, queries)

    logger.info(ls.INITIALIZED_PARSERS.format(languages=", ".join(available_languages)))
    return parsers, queries


def get_parser_and_language(
    language: cs.SupportedLanguage | str,
) -> tuple[Parser | None, Language | None]:
    if isinstance(language, str):
        try:
            language = cs.SupportedLanguage(language)
        except ValueError:
            return None, None

    if _is_windows_unsupported(language):
        return None, None

    lang_loader = LANGUAGE_LIBRARIES.get(language)
    if not lang_loader:
        return None, None

    try:
        lang_obj = Language(lang_loader())
        parser = Parser(lang_obj)
        return parser, lang_obj
    except Exception:
        return None, None
