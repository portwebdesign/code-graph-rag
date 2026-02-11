from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from codebase_rag.core import constants as cs
from codebase_rag.data_models.models import FQNSpec, LanguageSpec

if TYPE_CHECKING:
    from tree_sitter import Node


def _python_get_name(node: Node) -> str | None:
    """Extracts the name from a Python name-bearing node."""
    name_node = node.child_by_field_name("name")
    return (
        name_node.text.decode(cs.ENCODING_UTF8)
        if name_node and name_node.text
        else None
    )


def _python_file_to_module(file_path: Path, repo_root: Path) -> list[str]:
    """Converts a Python file path to a list of module parts for its FQN."""
    try:
        rel = file_path.relative_to(repo_root)
        parts = list(rel.with_suffix("").parts)
        if parts and parts[-1] == cs.INDEX_INIT:
            parts = parts[:-1]
        return parts
    except ValueError:
        return []


def _js_get_name(node: Node) -> str | None:
    """Extracts the name from a JavaScript/TypeScript name-bearing node."""
    if node.type in cs.JS_NAME_NODE_TYPES:
        name_node = node.child_by_field_name(cs.FIELD_NAME)
        return (
            name_node.text.decode(cs.ENCODING_UTF8)
            if name_node and name_node.text
            else None
        )
    return None


def _js_file_to_module(file_path: Path, repo_root: Path) -> list[str]:
    """Converts a JS/TS file path to a list of module parts for its FQN."""
    try:
        rel = file_path.relative_to(repo_root)
        parts = list(rel.with_suffix("").parts)
        if parts and parts[-1] == cs.INDEX_INDEX:
            parts = parts[:-1]
        return parts
    except ValueError:
        return []


def _generic_get_name(node: Node) -> str | None:
    """A generic function to extract a name from a node, trying common field names."""
    name_node = node.child_by_field_name("name")
    if name_node and name_node.text:
        return name_node.text.decode(cs.ENCODING_UTF8)

    for field_name in cs.NAME_FIELDS:
        name_node = node.child_by_field_name(field_name)
        if name_node and name_node.text:
            return name_node.text.decode(cs.ENCODING_UTF8)

    return None


def _generic_file_to_module(file_path: Path, repo_root: Path) -> list[str]:
    """A generic function to convert a file path to a list of module parts."""
    try:
        rel = file_path.relative_to(repo_root)
        return list(rel.with_suffix("").parts)
    except ValueError:
        return []


def _rust_get_name(node: Node) -> str | None:
    """Extracts the name from a Rust name-bearing node."""
    if node.type in cs.RS_TYPE_NODE_TYPES:
        name_node = node.child_by_field_name(cs.FIELD_NAME)
        if name_node and name_node.type == cs.TS_TYPE_IDENTIFIER and name_node.text:
            return name_node.text.decode(cs.ENCODING_UTF8)
    elif node.type in cs.RS_IDENT_NODE_TYPES:
        name_node = node.child_by_field_name(cs.FIELD_NAME)
        if name_node and name_node.type == cs.TS_IDENTIFIER and name_node.text:
            return name_node.text.decode(cs.ENCODING_UTF8)

    return _generic_get_name(node)


def _rust_file_to_module(file_path: Path, repo_root: Path) -> list[str]:
    """Converts a Rust file path to a list of module parts for its FQN."""
    try:
        rel = file_path.relative_to(repo_root)
        parts = list(rel.with_suffix("").parts)
        if parts and parts[-1] == cs.INDEX_MOD:
            parts = parts[:-1]
        return parts
    except ValueError:
        return []


def _cpp_get_name(node: Node) -> str | None:
    """Extracts the name from a C++ name-bearing node."""
    if node.type in cs.CPP_NAME_NODE_TYPES:
        name_node = node.child_by_field_name(cs.FIELD_NAME)
        if name_node and name_node.text:
            return name_node.text.decode(cs.ENCODING_UTF8)
    elif node.type == cs.TS_CPP_FUNCTION_DEFINITION:
        declarator = node.child_by_field_name(cs.FIELD_DECLARATOR)
        if declarator and declarator.type == cs.TS_CPP_FUNCTION_DECLARATOR:
            name_node = declarator.child_by_field_name(cs.FIELD_DECLARATOR)
            if name_node and name_node.type == cs.TS_IDENTIFIER and name_node.text:
                return name_node.text.decode(cs.ENCODING_UTF8)

    return _generic_get_name(node)


PYTHON_FQN_SPEC = FQNSpec(
    scope_node_types=frozenset(cs.FQN_PY_SCOPE_TYPES),
    function_node_types=frozenset(cs.FQN_PY_FUNCTION_TYPES),
    get_name=_python_get_name,
    file_to_module_parts=_python_file_to_module,
)

JS_FQN_SPEC = FQNSpec(
    scope_node_types=frozenset(cs.FQN_JS_SCOPE_TYPES),
    function_node_types=frozenset(cs.FQN_JS_FUNCTION_TYPES),
    get_name=_js_get_name,
    file_to_module_parts=_js_file_to_module,
)

TS_FQN_SPEC = FQNSpec(
    scope_node_types=frozenset(cs.FQN_TS_SCOPE_TYPES),
    function_node_types=frozenset(cs.FQN_TS_FUNCTION_TYPES),
    get_name=_js_get_name,
    file_to_module_parts=_js_file_to_module,
)

RUST_FQN_SPEC = FQNSpec(
    scope_node_types=frozenset(cs.FQN_RS_SCOPE_TYPES),
    function_node_types=frozenset(cs.FQN_RS_FUNCTION_TYPES),
    get_name=_rust_get_name,
    file_to_module_parts=_rust_file_to_module,
)

JAVA_FQN_SPEC = FQNSpec(
    scope_node_types=frozenset(cs.FQN_JAVA_SCOPE_TYPES),
    function_node_types=frozenset(cs.FQN_JAVA_FUNCTION_TYPES),
    get_name=_generic_get_name,
    file_to_module_parts=_generic_file_to_module,
)

CPP_FQN_SPEC = FQNSpec(
    scope_node_types=frozenset(cs.FQN_CPP_SCOPE_TYPES),
    function_node_types=frozenset(cs.FQN_CPP_FUNCTION_TYPES),
    get_name=_cpp_get_name,
    file_to_module_parts=_generic_file_to_module,
)

LUA_FQN_SPEC = FQNSpec(
    scope_node_types=frozenset(cs.FQN_LUA_SCOPE_TYPES),
    function_node_types=frozenset(cs.FQN_LUA_FUNCTION_TYPES),
    get_name=_generic_get_name,
    file_to_module_parts=_generic_file_to_module,
)

GO_FQN_SPEC = FQNSpec(
    scope_node_types=frozenset(cs.FQN_GO_SCOPE_TYPES),
    function_node_types=frozenset(cs.FQN_GO_FUNCTION_TYPES),
    get_name=_generic_get_name,
    file_to_module_parts=_generic_file_to_module,
)

SCALA_FQN_SPEC = FQNSpec(
    scope_node_types=frozenset(cs.FQN_SCALA_SCOPE_TYPES),
    function_node_types=frozenset(cs.FQN_SCALA_FUNCTION_TYPES),
    get_name=_generic_get_name,
    file_to_module_parts=_generic_file_to_module,
)

CSHARP_FQN_SPEC = FQNSpec(
    scope_node_types=frozenset(cs.FQN_CS_SCOPE_TYPES),
    function_node_types=frozenset(cs.FQN_CS_FUNCTION_TYPES),
    get_name=_generic_get_name,
    file_to_module_parts=_generic_file_to_module,
)

PHP_FQN_SPEC = FQNSpec(
    scope_node_types=frozenset(cs.FQN_PHP_SCOPE_TYPES),
    function_node_types=frozenset(cs.FQN_PHP_FUNCTION_TYPES),
    get_name=_generic_get_name,
    file_to_module_parts=_generic_file_to_module,
)

RUBY_FQN_SPEC = FQNSpec(
    scope_node_types=frozenset(cs.SPEC_RUBY_MODULE_TYPES),
    function_node_types=frozenset(cs.SPEC_RUBY_FUNCTION_TYPES),
    get_name=_generic_get_name,
    file_to_module_parts=_generic_file_to_module,
)

KOTLIN_FQN_SPEC = FQNSpec(
    scope_node_types=frozenset(cs.SPEC_KOTLIN_MODULE_TYPES),
    function_node_types=frozenset(cs.SPEC_KOTLIN_FUNCTION_TYPES),
    get_name=_generic_get_name,
    file_to_module_parts=_generic_file_to_module,
)

YAML_FQN_SPEC = FQNSpec(
    scope_node_types=frozenset(cs.SPEC_YAML_MODULE_TYPES),
    function_node_types=frozenset(cs.SPEC_YAML_FUNCTION_TYPES),
    get_name=_generic_get_name,
    file_to_module_parts=_generic_file_to_module,
)

JSON_FQN_SPEC = FQNSpec(
    scope_node_types=frozenset(cs.SPEC_JSON_MODULE_TYPES),
    function_node_types=frozenset(cs.SPEC_JSON_FUNCTION_TYPES),
    get_name=_generic_get_name,
    file_to_module_parts=_generic_file_to_module,
)

HTML_FQN_SPEC = FQNSpec(
    scope_node_types=frozenset(cs.SPEC_HTML_MODULE_TYPES),
    function_node_types=frozenset(cs.SPEC_HTML_FUNCTION_TYPES),
    get_name=_generic_get_name,
    file_to_module_parts=_generic_file_to_module,
)

CSS_FQN_SPEC = FQNSpec(
    scope_node_types=frozenset(cs.SPEC_CSS_MODULE_TYPES),
    function_node_types=frozenset(cs.SPEC_CSS_FUNCTION_TYPES),
    get_name=_generic_get_name,
    file_to_module_parts=_generic_file_to_module,
)

SCSS_FQN_SPEC = FQNSpec(
    scope_node_types=frozenset(cs.SPEC_SCSS_MODULE_TYPES),
    function_node_types=frozenset(cs.SPEC_SCSS_FUNCTION_TYPES),
    get_name=_generic_get_name,
    file_to_module_parts=_generic_file_to_module,
)

GRAPHQL_FQN_SPEC = FQNSpec(
    scope_node_types=frozenset(cs.SPEC_GRAPHQL_MODULE_TYPES),
    function_node_types=frozenset(cs.SPEC_GRAPHQL_FUNCTION_TYPES),
    get_name=_generic_get_name,
    file_to_module_parts=_generic_file_to_module,
)

DOCKERFILE_FQN_SPEC = FQNSpec(
    scope_node_types=frozenset(cs.SPEC_DOCKERFILE_MODULE_TYPES),
    function_node_types=frozenset(cs.SPEC_DOCKERFILE_FUNCTION_TYPES),
    get_name=_generic_get_name,
    file_to_module_parts=_generic_file_to_module,
)

SQL_FQN_SPEC = FQNSpec(
    scope_node_types=frozenset(cs.SPEC_SQL_MODULE_TYPES),
    function_node_types=frozenset(cs.SPEC_SQL_FUNCTION_TYPES),
    get_name=_generic_get_name,
    file_to_module_parts=_generic_file_to_module,
)

VUE_FQN_SPEC = FQNSpec(
    scope_node_types=frozenset(cs.SPEC_VUE_MODULE_TYPES),
    function_node_types=frozenset(cs.SPEC_VUE_FUNCTION_TYPES),
    get_name=_generic_get_name,
    file_to_module_parts=_generic_file_to_module,
)

SVELTE_FQN_SPEC = FQNSpec(
    scope_node_types=frozenset(cs.SPEC_SVELTE_MODULE_TYPES),
    function_node_types=frozenset(cs.SPEC_SVELTE_FUNCTION_TYPES),
    get_name=_generic_get_name,
    file_to_module_parts=_generic_file_to_module,
)

LANGUAGE_FQN_SPECS: dict[cs.SupportedLanguage, FQNSpec] = {
    cs.SupportedLanguage.PYTHON: PYTHON_FQN_SPEC,
    cs.SupportedLanguage.JS: JS_FQN_SPEC,
    cs.SupportedLanguage.TS: TS_FQN_SPEC,
    cs.SupportedLanguage.RUST: RUST_FQN_SPEC,
    cs.SupportedLanguage.JAVA: JAVA_FQN_SPEC,
    cs.SupportedLanguage.CPP: CPP_FQN_SPEC,
    cs.SupportedLanguage.LUA: LUA_FQN_SPEC,
    cs.SupportedLanguage.GO: GO_FQN_SPEC,
    cs.SupportedLanguage.SCALA: SCALA_FQN_SPEC,
    cs.SupportedLanguage.CSHARP: CSHARP_FQN_SPEC,
    cs.SupportedLanguage.PHP: PHP_FQN_SPEC,
    cs.SupportedLanguage.RUBY: RUBY_FQN_SPEC,
    cs.SupportedLanguage.KOTLIN: KOTLIN_FQN_SPEC,
    cs.SupportedLanguage.YAML: YAML_FQN_SPEC,
    cs.SupportedLanguage.JSON: JSON_FQN_SPEC,
    cs.SupportedLanguage.HTML: HTML_FQN_SPEC,
    cs.SupportedLanguage.CSS: CSS_FQN_SPEC,
    cs.SupportedLanguage.SCSS: SCSS_FQN_SPEC,
    cs.SupportedLanguage.GRAPHQL: GRAPHQL_FQN_SPEC,
    cs.SupportedLanguage.DOCKERFILE: DOCKERFILE_FQN_SPEC,
    cs.SupportedLanguage.SQL: SQL_FQN_SPEC,
    cs.SupportedLanguage.VUE: VUE_FQN_SPEC,
    cs.SupportedLanguage.SVELTE: SVELTE_FQN_SPEC,
}
"""A dictionary mapping supported languages to their FQN specifications."""


LANGUAGE_SPECS: dict[cs.SupportedLanguage, LanguageSpec] = {
    cs.SupportedLanguage.PYTHON: LanguageSpec(
        language=cs.SupportedLanguage.PYTHON,
        file_extensions=cs.PY_EXTENSIONS,
        function_node_types=cs.SPEC_PY_FUNCTION_TYPES,
        class_node_types=cs.SPEC_PY_CLASS_TYPES,
        module_node_types=cs.SPEC_PY_MODULE_TYPES,
        call_node_types=cs.SPEC_PY_CALL_TYPES,
        import_node_types=cs.SPEC_PY_IMPORT_TYPES,
        import_from_node_types=cs.SPEC_PY_IMPORT_FROM_TYPES,
        package_indicators=cs.SPEC_PY_PACKAGE_INDICATORS,
    ),
    cs.SupportedLanguage.JS: LanguageSpec(
        language=cs.SupportedLanguage.JS,
        file_extensions=cs.JS_EXTENSIONS,
        function_node_types=cs.JS_TS_FUNCTION_NODES,
        class_node_types=cs.JS_TS_CLASS_NODES,
        module_node_types=cs.SPEC_JS_MODULE_TYPES,
        call_node_types=cs.SPEC_JS_CALL_TYPES,
        import_node_types=cs.JS_TS_IMPORT_NODES,
        import_from_node_types=cs.JS_TS_IMPORT_NODES,
    ),
    cs.SupportedLanguage.TS: LanguageSpec(
        language=cs.SupportedLanguage.TS,
        file_extensions=cs.TS_EXTENSIONS,
        function_node_types=cs.JS_TS_FUNCTION_NODES + (cs.TS_FUNCTION_SIGNATURE,),
        class_node_types=cs.JS_TS_CLASS_NODES
        + (
            cs.TS_ABSTRACT_CLASS_DECLARATION,
            cs.TS_ENUM_DECLARATION,
            cs.TS_INTERFACE_DECLARATION,
            cs.TS_TYPE_ALIAS_DECLARATION,
            cs.TS_INTERNAL_MODULE,
        ),
        module_node_types=cs.SPEC_JS_MODULE_TYPES,
        call_node_types=cs.SPEC_JS_CALL_TYPES,
        import_node_types=cs.JS_TS_IMPORT_NODES,
        import_from_node_types=cs.JS_TS_IMPORT_NODES,
    ),
    cs.SupportedLanguage.RUST: LanguageSpec(
        language=cs.SupportedLanguage.RUST,
        file_extensions=cs.RS_EXTENSIONS,
        function_node_types=cs.SPEC_RS_FUNCTION_TYPES,
        class_node_types=cs.SPEC_RS_CLASS_TYPES,
        module_node_types=cs.SPEC_RS_MODULE_TYPES,
        call_node_types=cs.SPEC_RS_CALL_TYPES,
        import_node_types=cs.SPEC_RS_IMPORT_TYPES,
        import_from_node_types=cs.SPEC_RS_IMPORT_FROM_TYPES,
        package_indicators=cs.SPEC_RS_PACKAGE_INDICATORS,
        function_query="""
        (function_item
            name: (identifier) @name) @function
        (function_signature_item
            name: (identifier) @name) @function
        (closure_expression) @function
        """,
        class_query="""
        (struct_item
            name: (type_identifier) @name) @class
        (enum_item
            name: (type_identifier) @name) @class
        (union_item
            name: (type_identifier) @name) @class
        (trait_item
            name: (type_identifier) @name) @class
        (type_item
            name: (type_identifier) @name) @class
        (impl_item) @class
        (mod_item
            name: (identifier) @name) @module
        """,
        call_query="""
        (call_expression
            function: (identifier) @name) @call
        (call_expression
            function: (field_expression
                field: (field_identifier) @name)) @call
        (call_expression
            function: (scoped_identifier
                "::"
                name: (identifier) @name)) @call
        (macro_invocation
            macro: (identifier) @name) @call
        """,
    ),
    cs.SupportedLanguage.GO: LanguageSpec(
        language=cs.SupportedLanguage.GO,
        file_extensions=cs.GO_EXTENSIONS,
        function_node_types=cs.SPEC_GO_FUNCTION_TYPES,
        class_node_types=cs.SPEC_GO_CLASS_TYPES,
        module_node_types=cs.SPEC_GO_MODULE_TYPES,
        call_node_types=cs.SPEC_GO_CALL_TYPES,
        import_node_types=cs.SPEC_GO_IMPORT_TYPES,
        import_from_node_types=cs.SPEC_GO_IMPORT_TYPES,
    ),
    cs.SupportedLanguage.SCALA: LanguageSpec(
        language=cs.SupportedLanguage.SCALA,
        file_extensions=cs.SCALA_EXTENSIONS,
        function_node_types=cs.SPEC_SCALA_FUNCTION_TYPES,
        class_node_types=cs.SPEC_SCALA_CLASS_TYPES,
        module_node_types=cs.SPEC_SCALA_MODULE_TYPES,
        call_node_types=cs.SPEC_SCALA_CALL_TYPES,
        import_node_types=cs.SPEC_SCALA_IMPORT_TYPES,
        import_from_node_types=cs.SPEC_SCALA_IMPORT_TYPES,
    ),
    cs.SupportedLanguage.JAVA: LanguageSpec(
        language=cs.SupportedLanguage.JAVA,
        file_extensions=cs.JAVA_EXTENSIONS,
        function_node_types=cs.SPEC_JAVA_FUNCTION_TYPES,
        class_node_types=cs.SPEC_JAVA_CLASS_TYPES,
        module_node_types=cs.SPEC_JAVA_MODULE_TYPES,
        call_node_types=cs.SPEC_JAVA_CALL_TYPES,
        import_node_types=cs.SPEC_JAVA_IMPORT_TYPES,
        import_from_node_types=cs.SPEC_JAVA_IMPORT_TYPES,
        function_query="""
        (method_declaration
            name: (identifier) @name) @function
        (constructor_declaration
            name: (identifier) @name) @function
        """,
        class_query="""
        (class_declaration
            name: (identifier) @name) @class
        (interface_declaration
            name: (identifier) @name) @class
        (enum_declaration
            name: (identifier) @name) @class
        (annotation_type_declaration
            name: (identifier) @name) @class
        (record_declaration
            name: (identifier) @name) @class
        """,
        call_query="""
        (method_invocation
            name: (identifier) @name) @call
        (object_creation_expression
            type: (type_identifier) @name) @call
        """,
    ),
    cs.SupportedLanguage.CPP: LanguageSpec(
        language=cs.SupportedLanguage.CPP,
        file_extensions=cs.CPP_EXTENSIONS,
        function_node_types=cs.SPEC_CPP_FUNCTION_TYPES,
        class_node_types=cs.SPEC_CPP_CLASS_TYPES,
        module_node_types=cs.SPEC_CPP_MODULE_TYPES,
        call_node_types=cs.SPEC_CPP_CALL_TYPES,
        import_node_types=cs.CPP_IMPORT_NODES,
        import_from_node_types=cs.CPP_IMPORT_NODES,
        package_indicators=cs.SPEC_CPP_PACKAGE_INDICATORS,
        function_query="""
    (field_declaration) @function
    (declaration) @function
    (function_definition) @function
    (template_declaration (function_definition)) @function
    (lambda_expression) @function
    """,
        class_query="""
    (class_specifier) @class
    (struct_specifier) @class
    (union_specifier) @class
    (enum_specifier) @class
    (template_declaration (class_specifier)) @class
    (template_declaration (struct_specifier)) @class
    (template_declaration (union_specifier)) @class
    (template_declaration (enum_specifier)) @class
    """,
        call_query="""
    (call_expression) @call
    (binary_expression) @call
    (unary_expression) @call
    (update_expression) @call
    (field_expression) @call
    (subscript_expression) @call
    (new_expression) @call
    (delete_expression) @call
    """,
    ),
    cs.SupportedLanguage.CSHARP: LanguageSpec(
        language=cs.SupportedLanguage.CSHARP,
        file_extensions=cs.CS_EXTENSIONS,
        function_node_types=cs.SPEC_CS_FUNCTION_TYPES,
        class_node_types=cs.SPEC_CS_CLASS_TYPES,
        module_node_types=cs.SPEC_CS_MODULE_TYPES,
        call_node_types=cs.SPEC_CS_CALL_TYPES,
        import_node_types=cs.IMPORT_NODES_USING,
        import_from_node_types=cs.IMPORT_NODES_USING,
    ),
    cs.SupportedLanguage.PHP: LanguageSpec(
        language=cs.SupportedLanguage.PHP,
        file_extensions=cs.PHP_EXTENSIONS,
        function_node_types=cs.SPEC_PHP_FUNCTION_TYPES,
        class_node_types=cs.SPEC_PHP_CLASS_TYPES,
        module_node_types=cs.SPEC_PHP_MODULE_TYPES,
        call_node_types=cs.SPEC_PHP_CALL_TYPES,
    ),
    cs.SupportedLanguage.RUBY: LanguageSpec(
        language=cs.SupportedLanguage.RUBY,
        file_extensions=cs.RUBY_EXTENSIONS,
        function_node_types=cs.SPEC_RUBY_FUNCTION_TYPES,
        class_node_types=cs.SPEC_RUBY_CLASS_TYPES,
        module_node_types=cs.SPEC_RUBY_MODULE_TYPES,
        call_node_types=cs.SPEC_RUBY_CALL_TYPES,
        import_node_types=cs.SPEC_RUBY_IMPORT_TYPES,
        import_from_node_types=cs.SPEC_RUBY_IMPORT_TYPES,
    ),
    cs.SupportedLanguage.KOTLIN: LanguageSpec(
        language=cs.SupportedLanguage.KOTLIN,
        file_extensions=cs.KOTLIN_EXTENSIONS,
        function_node_types=cs.SPEC_KOTLIN_FUNCTION_TYPES,
        class_node_types=cs.SPEC_KOTLIN_CLASS_TYPES,
        module_node_types=cs.SPEC_KOTLIN_MODULE_TYPES,
        call_node_types=cs.SPEC_KOTLIN_CALL_TYPES,
        import_node_types=cs.SPEC_KOTLIN_IMPORT_TYPES,
        import_from_node_types=cs.SPEC_KOTLIN_IMPORT_TYPES,
    ),
    cs.SupportedLanguage.YAML: LanguageSpec(
        language=cs.SupportedLanguage.YAML,
        file_extensions=cs.YAML_EXTENSIONS,
        function_node_types=cs.SPEC_YAML_FUNCTION_TYPES,
        class_node_types=cs.SPEC_YAML_CLASS_TYPES,
        module_node_types=cs.SPEC_YAML_MODULE_TYPES,
        call_node_types=cs.SPEC_YAML_CALL_TYPES,
        import_node_types=cs.SPEC_YAML_IMPORT_TYPES,
        import_from_node_types=cs.SPEC_YAML_IMPORT_TYPES,
    ),
    cs.SupportedLanguage.JSON: LanguageSpec(
        language=cs.SupportedLanguage.JSON,
        file_extensions=cs.JSON_EXTENSIONS,
        function_node_types=cs.SPEC_JSON_FUNCTION_TYPES,
        class_node_types=cs.SPEC_JSON_CLASS_TYPES,
        module_node_types=cs.SPEC_JSON_MODULE_TYPES,
        call_node_types=cs.SPEC_JSON_CALL_TYPES,
        import_node_types=cs.SPEC_JSON_IMPORT_TYPES,
        import_from_node_types=cs.SPEC_JSON_IMPORT_TYPES,
    ),
    cs.SupportedLanguage.LUA: LanguageSpec(
        language=cs.SupportedLanguage.LUA,
        file_extensions=cs.LUA_EXTENSIONS,
        function_node_types=cs.SPEC_LUA_FUNCTION_TYPES,
        class_node_types=cs.SPEC_LUA_CLASS_TYPES,
        module_node_types=cs.SPEC_LUA_MODULE_TYPES,
        call_node_types=cs.SPEC_LUA_CALL_TYPES,
        import_node_types=cs.SPEC_LUA_IMPORT_TYPES,
    ),
    cs.SupportedLanguage.HTML: LanguageSpec(
        language=cs.SupportedLanguage.HTML,
        file_extensions=cs.HTML_EXTENSIONS,
        function_node_types=cs.SPEC_HTML_FUNCTION_TYPES,
        class_node_types=cs.SPEC_HTML_CLASS_TYPES,
        module_node_types=cs.SPEC_HTML_MODULE_TYPES,
        call_node_types=cs.SPEC_HTML_CALL_TYPES,
        import_node_types=cs.SPEC_HTML_IMPORT_TYPES,
        import_from_node_types=cs.SPEC_HTML_IMPORT_TYPES,
    ),
    cs.SupportedLanguage.CSS: LanguageSpec(
        language=cs.SupportedLanguage.CSS,
        file_extensions=cs.CSS_EXTENSIONS,
        function_node_types=cs.SPEC_CSS_FUNCTION_TYPES,
        class_node_types=cs.SPEC_CSS_CLASS_TYPES,
        module_node_types=cs.SPEC_CSS_MODULE_TYPES,
        call_node_types=cs.SPEC_CSS_CALL_TYPES,
        import_node_types=cs.SPEC_CSS_IMPORT_TYPES,
        import_from_node_types=cs.SPEC_CSS_IMPORT_TYPES,
    ),
    cs.SupportedLanguage.SCSS: LanguageSpec(
        language=cs.SupportedLanguage.SCSS,
        file_extensions=cs.SCSS_EXTENSIONS,
        function_node_types=cs.SPEC_SCSS_FUNCTION_TYPES,
        class_node_types=cs.SPEC_SCSS_CLASS_TYPES,
        module_node_types=cs.SPEC_SCSS_MODULE_TYPES,
        call_node_types=cs.SPEC_SCSS_CALL_TYPES,
        import_node_types=cs.SPEC_SCSS_IMPORT_TYPES,
        import_from_node_types=cs.SPEC_SCSS_IMPORT_TYPES,
    ),
    cs.SupportedLanguage.GRAPHQL: LanguageSpec(
        language=cs.SupportedLanguage.GRAPHQL,
        file_extensions=cs.GRAPHQL_EXTENSIONS,
        function_node_types=cs.SPEC_GRAPHQL_FUNCTION_TYPES,
        class_node_types=cs.SPEC_GRAPHQL_CLASS_TYPES,
        module_node_types=cs.SPEC_GRAPHQL_MODULE_TYPES,
        call_node_types=cs.SPEC_GRAPHQL_CALL_TYPES,
        import_node_types=cs.SPEC_GRAPHQL_IMPORT_TYPES,
        import_from_node_types=cs.SPEC_GRAPHQL_IMPORT_TYPES,
    ),
    cs.SupportedLanguage.DOCKERFILE: LanguageSpec(
        language=cs.SupportedLanguage.DOCKERFILE,
        file_extensions=cs.DOCKERFILE_EXTENSIONS,
        function_node_types=cs.SPEC_DOCKERFILE_FUNCTION_TYPES,
        class_node_types=cs.SPEC_DOCKERFILE_CLASS_TYPES,
        module_node_types=cs.SPEC_DOCKERFILE_MODULE_TYPES,
        call_node_types=cs.SPEC_DOCKERFILE_CALL_TYPES,
        import_node_types=cs.SPEC_DOCKERFILE_IMPORT_TYPES,
        import_from_node_types=cs.SPEC_DOCKERFILE_IMPORT_TYPES,
    ),
    cs.SupportedLanguage.SQL: LanguageSpec(
        language=cs.SupportedLanguage.SQL,
        file_extensions=cs.SQL_EXTENSIONS,
        function_node_types=cs.SPEC_SQL_FUNCTION_TYPES,
        class_node_types=cs.SPEC_SQL_CLASS_TYPES,
        module_node_types=cs.SPEC_SQL_MODULE_TYPES,
        call_node_types=cs.SPEC_SQL_CALL_TYPES,
        import_node_types=cs.SPEC_SQL_IMPORT_TYPES,
        import_from_node_types=cs.SPEC_SQL_IMPORT_TYPES,
    ),
    cs.SupportedLanguage.VUE: LanguageSpec(
        language=cs.SupportedLanguage.VUE,
        file_extensions=cs.VUE_EXTENSIONS,
        function_node_types=cs.SPEC_VUE_FUNCTION_TYPES,
        class_node_types=cs.SPEC_VUE_CLASS_TYPES,
        module_node_types=cs.SPEC_VUE_MODULE_TYPES,
        call_node_types=cs.SPEC_VUE_CALL_TYPES,
        import_node_types=cs.SPEC_VUE_IMPORT_TYPES,
        import_from_node_types=cs.SPEC_VUE_IMPORT_TYPES,
    ),
    cs.SupportedLanguage.SVELTE: LanguageSpec(
        language=cs.SupportedLanguage.SVELTE,
        file_extensions=cs.SVELTE_EXTENSIONS,
        function_node_types=cs.SPEC_SVELTE_FUNCTION_TYPES,
        class_node_types=cs.SPEC_SVELTE_CLASS_TYPES,
        module_node_types=cs.SPEC_SVELTE_MODULE_TYPES,
        call_node_types=cs.SPEC_SVELTE_CALL_TYPES,
        import_node_types=cs.SPEC_SVELTE_IMPORT_TYPES,
        import_from_node_types=cs.SPEC_SVELTE_IMPORT_TYPES,
    ),
}
"""A dictionary mapping supported languages to their parsing and structural specifications."""

_EXTENSION_TO_SPEC: dict[str, LanguageSpec] = {}
for _config in LANGUAGE_SPECS.values():
    for _ext in _config.file_extensions:
        _EXTENSION_TO_SPEC[_ext] = _config

_FILENAME_TO_SPEC: dict[str, LanguageSpec] = {
    cs.DOCKERFILE_NAME: LANGUAGE_SPECS[cs.SupportedLanguage.DOCKERFILE],
}


def get_language_spec(file_extension: str) -> LanguageSpec | None:
    """
    Retrieves the LanguageSpec for a given file extension.

    Args:
        file_extension (str): The file extension (e.g., '.py', '.js').

    Returns:
        LanguageSpec | None: The corresponding language specification, or None if not found.
    """
    return _EXTENSION_TO_SPEC.get(file_extension)


def get_language_spec_for_path(file_path: Path) -> LanguageSpec | None:
    if file_path.suffix:
        return get_language_spec(file_path.suffix)
    return _FILENAME_TO_SPEC.get(file_path.name)


def get_language_for_extension(file_extension: str) -> cs.SupportedLanguage | None:
    """
    Retrieves the SupportedLanguage enum for a given file extension.

    Args:
        file_extension (str): The file extension.

    Returns:
        cs.SupportedLanguage | None: The language enum, or None if not found.
    """
    spec = _EXTENSION_TO_SPEC.get(file_extension)
    if spec and isinstance(spec.language, cs.SupportedLanguage):
        return spec.language
    return None
