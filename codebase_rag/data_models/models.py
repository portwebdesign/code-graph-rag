"""
This module defines the core data models used throughout the application.

Using `dataclass` and `NamedTuple`, it provides structured, type-hinted classes
for representing various concepts such as application state, graph elements,
language specifications, and tool metadata. These models help ensure data
consistency and improve code clarity.

The models include:
-   `SessionState` and `AppContext`: For managing application-wide state and context.
-   `GraphNode` and `GraphRelationship`: To represent elements of the code graph.
-   `LanguageSpec`: To define the parsing characteristics and node types for a
    specific programming language.
-   `FQNSpec`: For defining how to construct Fully Qualified Names (FQNs) for a
    language.
-   `ToolMetadata`: To hold the schema, description, and handler for an agent tool.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from rich.console import Console

from codebase_rag.core.constants import SupportedLanguage

from .types_defs import MCPHandlerType, MCPInputSchema, PropertyValue

if TYPE_CHECKING:
    from tree_sitter import Node


@dataclass
class SessionState:
    """
    Manages the state for the current application session.

    Attributes:
        confirm_edits (bool): Whether to prompt the user for confirmation before
                              making file edits.
        log_file (Path | None): The path to the session's log file.
        cancelled (bool): A flag indicating if the current operation has been
                          cancelled by the user.
    """

    confirm_edits: bool = True
    log_file: Path | None = None
    cancelled: bool = False

    def reset_cancelled(self) -> None:
        """Resets the `cancelled` flag to False."""
        self.cancelled = False


def _default_console() -> Console:
    """Creates a default Rich Console instance."""
    return Console(width=None, force_terminal=True)


@dataclass
class AppContext:
    """
    Holds the global application context, including session state and console.

    Attributes:
        session (SessionState): The state for the current session.
        console (Console): The Rich console instance for styled output.
    """

    session: SessionState = field(default_factory=SessionState)
    console: Console = field(default_factory=_default_console)


@dataclass
class GraphNode:
    """
    Represents a node in the in-memory graph representation.

    Attributes:
        node_id (int): The unique identifier of the node.
        labels (list[str]): The list of labels associated with the node (e.g., 'Class').
        properties (dict): A dictionary of the node's properties.
    """

    node_id: int
    labels: list[str]
    properties: dict[str, PropertyValue]


@dataclass
class GraphRelationship:
    """
    Represents a relationship in the in-memory graph representation.

    Attributes:
        from_id (int): The ID of the source node.
        to_id (int): The ID of the target node.
        type (str): The type of the relationship (e.g., 'CALLS').
        properties (dict): A dictionary of the relationship's properties.
    """

    from_id: int
    to_id: int
    type: str
    properties: dict[str, PropertyValue]


class FQNSpec(NamedTuple):
    """
    Defines the specification for resolving Fully Qualified Names (FQNs) for a language.

    Attributes:
        scope_node_types (frozenset[str]): Tree-sitter node types that define a new scope
                                           (e.g., 'class_declaration', 'module').
        function_node_types (frozenset[str]): Tree-sitter node types for functions/methods.
        get_name (Callable): A function to extract the name from a name-bearing node.
        file_to_module_parts (Callable): A function to convert a file path into a list
                                         of module parts for the FQN.
    """

    scope_node_types: frozenset[str]
    function_node_types: frozenset[str]
    get_name: Callable[["Node"], str | None]
    file_to_module_parts: Callable[[Path, Path], list[str]]


@dataclass(frozen=True)
class LanguageSpec:
    """
    Defines the parsing and structural characteristics for a programming language.

    This is a central model used to configure how tree-sitter parses a language
    and how different code constructs are identified.

    Attributes:
        language (SupportedLanguage | str): The name of the language.
        file_extensions (tuple[str, ...]): File extensions associated with the language.
        function_node_types (tuple[str, ...]): Tree-sitter node types for functions.
        class_node_types (tuple[str, ...]): Tree-sitter node types for classes/structs.
        module_node_types (tuple[str, ...]): Tree-sitter node types for modules.
        call_node_types (tuple[str, ...]): Tree-sitter node types for function/method calls.
        import_node_types (tuple[str, ...]): Node types for simple import statements.
        import_from_node_types (tuple[str, ...]): Node types for 'from ... import' statements.
        name_field (str): The child field name of a node that holds its name.
        body_field (str): The child field name of a node that holds its body.
        package_indicators (tuple[str, ...]): Filenames that indicate a directory is a package.
        function_query (str | None): An optional, overriding tree-sitter query for functions.
        class_query (str | None): An optional, overriding tree-sitter query for classes.
        call_query (str | None): An optional, overriding tree-sitter query for calls.
    """

    language: SupportedLanguage | str
    file_extensions: tuple[str, ...]
    function_node_types: tuple[str, ...]
    class_node_types: tuple[str, ...]
    module_node_types: tuple[str, ...]
    call_node_types: tuple[str, ...] = ()
    import_node_types: tuple[str, ...] = ()
    import_from_node_types: tuple[str, ...] = ()
    name_field: str = "name"
    body_field: str = "body"
    package_indicators: tuple[str, ...] = ()
    function_query: str | None = None
    class_query: str | None = None
    call_query: str | None = None


@dataclass
class Dependency:
    """
    Represents a project dependency.

    Attributes:
        name (str): The name of the dependency package.
        spec (str): The version specifier (e.g., '>=1.0.0').
        properties (dict[str, str]): Additional properties of the dependency.
    """

    name: str
    spec: str
    properties: dict[str, str] = field(default_factory=dict)


@dataclass
class MethodModifiersAndAnnotations:
    """
    Holds modifiers and annotations for a method, primarily used for Java parsing.

    Attributes:
        modifiers (list[str]): A list of modifiers (e.g., 'public', 'static').
        annotations (list[str]): A list of annotations (e.g., '@Override').
    """

    modifiers: list[str] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)


@dataclass
class ToolMetadata:
    """
    Represents the metadata for a tool available to the LLM agent.

    Attributes:
        name (str): The name of the tool.
        description (str): A description of what the tool does.
        input_schema (MCPInputSchema): The schema for the tool's input arguments.
        handler (MCPHandlerType): The asynchronous function that executes the tool's logic.
        returns_json (bool): A flag indicating if the tool's output should be
                             formatted as a JSON string.
    """

    name: str
    description: str
    input_schema: MCPInputSchema
    handler: MCPHandlerType
    returns_json: bool
