"""
This module provides utility functions specifically for handling C++20 modules.

It contains logic to:
-   Find and parse `export module` and `module` declarations.
-   Ingest `ModuleInterface` and `ModuleImplementation` nodes into the graph.
-   Create the appropriate `EXPORTS_MODULE` and `IMPLEMENTS_MODULE` relationships.
-   Identify classes that are explicitly exported from a module.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from tree_sitter import Node

from ...core import constants as cs
from ...core import logs
from ..utils import safe_decode_text, safe_decode_with_fallback
from .utils import decode_node_stripped

if TYPE_CHECKING:
    from ...services import IngestorProtocol


def ingest_cpp_module_declarations(
    root_node: Node,
    module_qn: str,
    file_path: Path,
    repo_path: Path,
    project_name: str,
    ingestor: IngestorProtocol,
) -> None:
    """
    Finds and ingests C++ module declarations from a file's AST.

    Args:
        root_node (Node): The root node of the file's AST.
        module_qn (str): The qualified name of the containing file-as-a-module.
        file_path (Path): The path to the source file.
        repo_path (Path): The root path of the repository.
        project_name (str): The name of the project.
        ingestor (IngestorProtocol): The data ingestion service.
    """
    module_declarations = _find_module_declarations(root_node)

    for _, decl_text in module_declarations:
        if decl_text.startswith(cs.CPP_EXPORT_MODULE_PREFIX):
            _process_export_module(
                decl_text, module_qn, file_path, repo_path, project_name, ingestor
            )
        elif decl_text.startswith(cs.CPP_MODULE_PREFIX) and not decl_text.startswith(
            cs.CPP_MODULE_PRIVATE_PREFIX
        ):
            _process_module_implementation(
                decl_text, module_qn, file_path, repo_path, project_name, ingestor
            )


def _find_module_declarations(root_node: Node) -> list[tuple[Node, str]]:
    """
    Recursively finds all C++ module declaration nodes in the AST.

    Args:
        root_node (Node): The node to start the search from.

    Returns:
        list[tuple[Node, str]]: A list of tuples, each containing the found
                                node and its decoded text.
    """
    module_declarations: list[tuple[Node, str]] = []

    def find_declarations(node: Node) -> None:
        if node.type == cs.TS_MODULE_DECLARATION:
            module_declarations.append((node, decode_node_stripped(node)))
        elif node.type == cs.CppNodeType.DECLARATION:
            has_module = any(
                child.type == cs.ONEOF_MODULE
                or (
                    child.text
                    and safe_decode_with_fallback(child).strip() == cs.ONEOF_MODULE
                )
                for child in node.children
            )
            if has_module:
                module_declarations.append((node, decode_node_stripped(node)))

        for child in node.children:
            find_declarations(child)

    find_declarations(root_node)
    return module_declarations


def _process_export_module(
    decl_text: str,
    module_qn: str,
    file_path: Path,
    repo_path: Path,
    project_name: str,
    ingestor: IngestorProtocol,
) -> None:
    """Processes an `export module` declaration."""
    parts = decl_text.split()
    if len(parts) < 3:
        return

    module_name = parts[2].rstrip(cs.CHAR_SEMICOLON)
    interface_qn = f"{project_name}.{module_name}"

    ingestor.ensure_node_batch(
        cs.NodeLabel.MODULE_INTERFACE,
        {
            cs.KEY_QUALIFIED_NAME: interface_qn,
            cs.KEY_NAME: module_name,
            cs.KEY_PATH: str(file_path.relative_to(repo_path)),
            cs.KEY_MODULE_TYPE: cs.CPP_MODULE_TYPE_INTERFACE,
        },
    )

    ingestor.ensure_relationship_batch(
        (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
        cs.RelationshipType.EXPORTS_MODULE,
        (cs.NodeLabel.MODULE_INTERFACE, cs.KEY_QUALIFIED_NAME, interface_qn),
    )

    logger.info(logs.CLASS_CPP_MODULE_INTERFACE.format(qn=interface_qn))


def _process_module_implementation(
    decl_text: str,
    module_qn: str,
    file_path: Path,
    repo_path: Path,
    project_name: str,
    ingestor: IngestorProtocol,
) -> None:
    """Processes a `module` implementation declaration."""
    parts = decl_text.split()
    if len(parts) < 2:
        return

    module_name = parts[1].rstrip(cs.CHAR_SEMICOLON)
    impl_qn = f"{project_name}.{module_name}{cs.CPP_IMPL_SUFFIX}"

    ingestor.ensure_node_batch(
        cs.NodeLabel.MODULE_IMPLEMENTATION,
        {
            cs.KEY_QUALIFIED_NAME: impl_qn,
            cs.KEY_NAME: f"{module_name}{cs.CPP_IMPL_SUFFIX}",
            cs.KEY_PATH: str(file_path.relative_to(repo_path)),
            cs.KEY_IMPLEMENTS_MODULE: module_name,
            cs.KEY_MODULE_TYPE: cs.CPP_MODULE_TYPE_IMPLEMENTATION,
        },
    )

    ingestor.ensure_relationship_batch(
        (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
        cs.RelationshipType.IMPLEMENTS_MODULE,
        (cs.NodeLabel.MODULE_IMPLEMENTATION, cs.KEY_QUALIFIED_NAME, impl_qn),
    )

    interface_qn = f"{project_name}.{module_name}"
    ingestor.ensure_relationship_batch(
        (cs.NodeLabel.MODULE_IMPLEMENTATION, cs.KEY_QUALIFIED_NAME, impl_qn),
        cs.RelationshipType.IMPLEMENTS,
        (cs.NodeLabel.MODULE_INTERFACE, cs.KEY_QUALIFIED_NAME, interface_qn),
    )

    logger.info(logs.CLASS_CPP_MODULE_IMPL.format(qn=impl_qn))


def find_cpp_exported_classes(root_node: Node) -> list[Node]:
    """
    Finds C++ classes that are exported using the `export` keyword.

    This is a workaround for tree-sitter's parsing of `export class ...` as
    a function definition with an error node.

    Args:
        root_node (Node): The root node of the AST to search.

    Returns:
        list[Node]: A list of AST nodes corresponding to exported classes.
    """
    exported_class_nodes: list[Node] = []

    def traverse(node: Node) -> None:
        if node.type == cs.CppNodeType.FUNCTION_DEFINITION:
            node_text = decode_node_stripped(node)

            if node_text.startswith(cs.CPP_EXPORT_PREFIXES):
                for child in node.children:
                    if child.type == cs.TS_ERROR and child.text:
                        error_text = safe_decode_text(child)
                        if error_text in cs.CPP_EXPORTED_CLASS_KEYWORDS:
                            exported_class_nodes.append(node)
                            break
                else:
                    if (
                        cs.CPP_EXPORT_CLASS_PREFIX in node_text
                        or cs.CPP_EXPORT_STRUCT_PREFIX in node_text
                    ):
                        exported_class_nodes.append(node)

        for child in node.children:
            traverse(child)

    traverse(root_node)
    return exported_class_nodes
