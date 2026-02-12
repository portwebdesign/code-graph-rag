from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from tree_sitter import Node

from codebase_rag.core import constants as cs
from codebase_rag.parsers.core.utils import safe_decode_text, safe_decode_with_fallback

from ... import logs
from .utils import decode_node_stripped

if TYPE_CHECKING:
    from codebase_rag.services import IngestorProtocol


def ingest_cpp_module_declarations(
    root_node: Node,
    module_qn: str,
    file_path: Path,
    repo_path: Path,
    project_name: str,
    ingestor: IngestorProtocol,
) -> None:
    """
    Ingest C++ module declarations from the given AST root node.

    Finds module declarations (export module ... / module ...), processes them,
    and creates appropriate nodes (MODULE_INTERFACE, MODULE_IMPLEMENTATION)
    and relationships in the graph.

    Args:
        root_node: The root AST node of the C++ file.
        module_qn: The qualified name of the module being processed.
        file_path: The path to the C++ source file.
        repo_path: The root path of the repository.
        project_name: The name of the project.
        ingestor: The ingestor instance to use for creating nodes and relationships.
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
    Find all module declaration nodes and their text content in the AST.

    Args:
        root_node: The root AST node to search.

    Returns:
        A list of tuples containing the module declaration Node and its decoded text string.
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
    """
    Process an exported module interface declaration.

    Creates a MODULE_INTERFACE node and links it to the defining module with an EXPORTS_MODULE relationship.

    Args:
        decl_text: The text content of the declaration (e.g., 'export module MyMod;').
        module_qn: The qualified name of the defining module.
        file_path: The file path.
        repo_path: The repository root path.
        project_name: The project name.
        ingestor: The ingestor instance.
    """
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
            cs.KEY_PATH: file_path.relative_to(repo_path).as_posix(),
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
    """
    Process a module implementation declaration.

    Creates a MODULE_IMPLEMENTATION node and links it to the implemented module interface.

    Args:
        decl_text: The text content of the declaration (e.g., 'module MyMod;').
        module_qn: The qualified name of the defining module.
        file_path: The file path.
        repo_path: The repository root path.
        project_name: The project name.
        ingestor: The ingestor instance.
    """
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
            cs.KEY_PATH: file_path.relative_to(repo_path).as_posix(),
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
    Find C++ classes or structs that are explicitly exported.

    Looks for `export class ...` or `export struct ...` patterns, including
    those using macro-based exports if detected via specific keywords.

    Args:
        root_node: The root AST node.

    Returns:
        A list of class/struct Nodes that are exported.
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
