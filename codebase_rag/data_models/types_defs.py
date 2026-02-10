"""
This module defines a collection of shared type definitions, protocols, and data
structures used throughout the application.

It centralizes type hints for complex data shapes, including those for graph data,
language model configurations, tool arguments, and various API results. Using
`TypedDict`, `NamedTuple`, `Protocol`, and `dataclass`, it provides static type
checking support and improves code readability and maintainability.

The definitions include:
-   Basic types for graph properties, node/relationship data, and query results.
-   Protocols for abstracting interfaces like database cursors, caches, and registries.
-   Typed dictionaries for structured data like model configurations, API responses,
    and parsed code information.
-   Named tuples for simple, immutable data structures.
-   Enumerations for controlled vocabularies like node types.
-   Schema definitions for graph nodes and relationships.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable, ItemsView, KeysView, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Protocol, TypedDict

from prompt_toolkit.styles import Style

from codebase_rag.core.constants import NodeLabel, RelationshipType, SupportedLanguage

if TYPE_CHECKING:
    from tree_sitter import Language, Node, Parser, Query

    from .models import LanguageSpec

# Basic type aliases
type LanguageLoader = Callable[[], Language] | None
"""A callable that returns a tree-sitter Language object, or None."""

PropertyValue = str | int | float | bool | list[str] | None
"""A type for values that can be stored as properties in the graph."""

PropertyDict = dict[str, PropertyValue]
"""A dictionary representing properties of a node or relationship."""

type ResultScalar = str | int | float | bool | None
"""A scalar value that can be returned from a database query."""

type ResultValue = ResultScalar | list[ResultScalar] | dict[str, ResultScalar]
"""A value in a database result row, which can be a scalar, list, or dict."""

type ResultRow = dict[str, ResultValue]
"""A single row from a database query result, represented as a dictionary."""


class FunctionMatch(TypedDict):
    """Represents a matched function or method from AST parsing."""

    node: Node
    simple_name: str
    qualified_name: str
    parent_class: str | None
    line_number: int


# Types for batch database operations
class NodeBatchRow(TypedDict):
    """A row for batch-creating nodes."""

    id: PropertyValue
    props: PropertyDict


class RelBatchRow(TypedDict):
    """A row for batch-creating relationships."""

    from_val: PropertyValue
    to_val: PropertyValue
    props: PropertyDict


BatchParams = NodeBatchRow | RelBatchRow | PropertyDict
"""A union of possible parameter types for batch operations."""


class BatchWrapper(TypedDict):
    """A wrapper for a batch of parameters, often required by database drivers."""

    batch: Sequence[BatchParams]


# Types for function and name lookups
type SimpleName = str
"""A simple, unqualified name of a function, class, etc."""

type QualifiedName = str
"""A fully qualified name (e.g., 'my_project.module.ClassName.method')."""

type SimpleNameLookup = defaultdict[SimpleName, set[QualifiedName]]
"""A mapping from simple names to a set of qualified names."""

NodeIdentifier = tuple[NodeLabel | str, str, str | None]
"""A tuple used to uniquely identify a node: (label, primary_property, value)."""

type ASTNode = Node
"""An alias for a tree-sitter Node for clarity."""


class NodeType(StrEnum):
    """Enumeration for the types of nodes that can be defined in the code graph."""

    FUNCTION = "Function"
    METHOD = "Method"
    CLASS = "Class"
    MODULE = "Module"
    INTERFACE = "Interface"
    PACKAGE = "Package"
    ENUM = "Enum"
    TYPE = "Type"
    UNION = "Union"


# Trie and registry types
type TrieNode = dict[str, TrieNode | QualifiedName | NodeType]
"""A node in the FunctionRegistryTrie."""

type FunctionRegistry = dict[QualifiedName, NodeType]
"""A direct mapping from qualified names to their node types."""


# Protocols for structural typing
class FunctionRegistryTrieProtocol(Protocol):
    """A protocol defining the interface for the function registry."""

    def __contains__(self, qualified_name: QualifiedName) -> bool: ...
    def __getitem__(self, qualified_name: QualifiedName) -> NodeType: ...

    def __setitem__(
        self, qualified_name: QualifiedName, func_type: NodeType
    ) -> None: ...

    def get(
        self, qualified_name: QualifiedName, default: NodeType | None = None
    ) -> NodeType | None: ...
    def keys(self) -> KeysView[QualifiedName]: ...
    def items(self) -> ItemsView[QualifiedName, NodeType]: ...
    def find_with_prefix(self, prefix: str) -> list[tuple[QualifiedName, NodeType]]: ...

    def find_ending_with(self, suffix: str) -> list[QualifiedName]: ...


class ASTCacheProtocol(Protocol):
    """A protocol defining the interface for the AST cache."""

    def __setitem__(self, key: Path, value: tuple[Node, SupportedLanguage]) -> None: ...

    def __getitem__(self, key: Path) -> tuple[Node, SupportedLanguage]: ...
    def __delitem__(self, key: Path) -> None: ...
    def __contains__(self, key: Path) -> bool: ...
    def items(self) -> ItemsView[Path, tuple[Node, SupportedLanguage]]: ...


class ColumnDescriptor(Protocol):
    """A protocol for a database cursor's column description."""

    @property
    def name(self) -> str: ...


class LoadableProtocol(Protocol):
    """A protocol for objects that require an explicit loading step."""

    def _ensure_loaded(self) -> None: ...


class CursorProtocol(Protocol):
    """A protocol for a database cursor, abstracting over different DB drivers."""

    def execute(
        self,
        query: str,
        params: dict[str, PropertyValue]
        | Sequence[BatchParams]
        | BatchWrapper
        | None = None,
    ) -> None: ...
    def close(self) -> None: ...
    @property
    def description(self) -> Sequence[ColumnDescriptor] | None: ...
    def fetchall(self) -> list[tuple[PropertyValue, ...]]: ...


class PathValidatorProtocol(Protocol):
    """A protocol for objects that validate file paths against a project root."""

    @property
    def project_root(self) -> Path: ...


class TreeSitterNodeProtocol(Protocol):
    """A protocol defining the essential properties of a tree-sitter Node."""

    @property
    def type(self) -> str: ...
    @property
    def children(self) -> list[TreeSitterNodeProtocol]: ...
    @property
    def text(self) -> bytes: ...


# Model and tool configuration types
class ModelConfigKwargs(TypedDict, total=False):
    """Keyword arguments for configuring or updating a language model."""

    api_key: str | None
    endpoint: str | None
    project_id: str | None
    region: str | None
    provider_type: str | None
    thinking_budget: int | None
    service_account_file: str | None


# Graph data structures
class GraphMetadata(TypedDict):
    """Metadata associated with an exported graph."""

    total_nodes: int
    total_relationships: int
    exported_at: str


class NodeData(TypedDict):
    """The structure of a single node in an exported graph JSON."""

    node_id: int
    labels: list[str]
    properties: dict[str, PropertyValue]


class RelationshipData(TypedDict):
    """The structure of a single relationship in an exported graph JSON."""

    from_id: int
    to_id: int
    type: str
    properties: dict[str, PropertyValue]


class GraphData(TypedDict):
    """The overall structure of an exported graph JSON file."""

    nodes: list[NodeData] | list[ResultRow]
    relationships: list[RelationshipData] | list[ResultRow]
    metadata: GraphMetadata


class GraphSummary(TypedDict):
    """A summary of the graph's contents."""

    total_nodes: int
    total_relationships: int
    node_labels: dict[str, int]
    relationship_types: dict[str, int]
    metadata: GraphMetadata


# Search and embedding result types
class EmbeddingQueryResult(TypedDict):
    """The result of a query for data to be embedded."""

    node_id: int
    qualified_name: str
    start_line: int | None
    end_line: int | None
    path: str | None


class SemanticSearchResult(TypedDict):
    """A single result from a semantic (vector) search."""

    node_id: int
    qualified_name: str
    name: str
    type: str
    score: float


# Language-specific parsing result types (Java example)
class JavaClassInfo(TypedDict):
    """Information extracted for a Java class."""

    name: str | None
    type: str
    superclass: str | None
    interfaces: list[str]
    modifiers: list[str]
    type_parameters: list[str]


class JavaMethodInfo(TypedDict):
    """Information extracted for a Java method."""

    name: str | None
    type: str
    return_type: str | None
    parameters: list[str]
    modifiers: list[str]
    type_parameters: list[str]
    annotations: list[str]


class JavaFieldInfo(TypedDict):
    """Information extracted for a Java field."""

    name: str | None
    type: str | None
    modifiers: list[str]
    annotations: list[str]


class JavaAnnotationInfo(TypedDict):
    """Information extracted for a Java annotation."""

    name: str | None
    arguments: list[str]


class JavaMethodCallInfo(TypedDict):
    """Information extracted for a Java method call."""

    name: str | None
    object: str | None
    arguments: int


# Agent and UI related types
class CancelledResult(NamedTuple):
    """A result indicating that an operation was cancelled."""

    cancelled: bool


class CgrignorePatterns(NamedTuple):
    """Patterns for excluding and including files, loaded from .cgrignore."""

    exclude: frozenset[str]
    unignore: frozenset[str]


class AgentLoopUI(NamedTuple):
    """UI elements for an agent's interaction loop."""

    status_message: str
    cancelled_log: str
    approval_prompt: str
    denial_default: str
    panel_title: str


ORANGE_STYLE = Style.from_dict({"": "#ff8c00"})

OPTIMIZATION_LOOP_UI = AgentLoopUI(
    status_message="[bold green]Agent is analyzing codebase... (Press Ctrl+C to cancel)[/bold green]",
    cancelled_log="ASSISTANT: [Analysis was cancelled]",
    approval_prompt="Do you approve this optimization?",
    denial_default="User rejected this optimization without feedback",
    panel_title="[bold green]Optimization Agent[/bold green]",
)

CHAT_LOOP_UI = AgentLoopUI(
    status_message="[bold green]Thinking... (Press Ctrl+C to cancel)[/bold green]",
    cancelled_log="ASSISTANT: [Thinking was cancelled]",
    approval_prompt="Do you approve this change?",
    denial_default="User rejected this change without feedback",
    panel_title="[bold green]Assistant[/bold green]",
)


# Parser and query loading types
class LanguageImport(NamedTuple):
    """Information needed to import a tree-sitter language grammar."""

    lang_key: SupportedLanguage
    module_path: str
    attr_name: str
    submodule_name: SupportedLanguage


# Tool definition types
class ToolNames(NamedTuple):
    """Standardized names for tools available to the LLM agent."""

    query_graph: str
    read_file: str
    analyze_document: str
    semantic_search: str
    create_file: str
    edit_file: str
    shell_command: str


class ConfirmationToolNames(NamedTuple):
    """Names of tools that require user confirmation before execution."""

    replace_code: str
    create_file: str
    shell_command: str


class ReplaceCodeArgs(TypedDict, total=False):
    """Arguments for the 'replace_code' tool."""

    file_path: str
    target_code: str
    replacement_code: str


class CreateFileArgs(TypedDict, total=False):
    """Arguments for the 'create_file' tool."""

    file_path: str
    content: str


class ShellCommandArgs(TypedDict, total=False):
    """Arguments for the 'shell_command' tool."""

    command: str


@dataclass
class RawToolArgs:
    """A dataclass to hold raw, unparsed arguments for any tool."""

    file_path: str = ""
    target_code: str = ""
    replacement_code: str = ""
    content: str = ""
    command: str = ""


ToolArgs = ReplaceCodeArgs | CreateFileArgs | ShellCommandArgs
"""A union of all possible tool argument types."""


class LanguageQueries(TypedDict):
    """A collection of tree-sitter queries for a specific language."""

    functions: Query | None
    classes: Query | None
    calls: Query | None
    imports: Query | None
    locals: Query | None
    config: LanguageSpec
    language: Language
    parser: Parser


class FunctionNodeProps(TypedDict, total=False):
    """Properties for creating a 'Function' or 'Method' node in the graph."""

    qualified_name: str
    name: str | None
    start_line: int
    end_line: int
    docstring: str | None


# Types for the Multi-turn Conversation Protocol (MCP)
MCPToolArguments = dict[str, str | int | None]
"""Arguments for a tool call in the MCP format."""


class MCPInputSchemaProperty(TypedDict, total=False):
    """A single property within an MCP tool's input schema."""

    type: str
    description: str
    default: str


MCPInputSchemaProperties = dict[str, MCPInputSchemaProperty]
"""A dictionary of properties for an MCP tool's input schema."""


class MCPInputSchema(TypedDict):
    """The input schema for an MCP tool."""

    type: str
    properties: MCPInputSchemaProperties
    required: list[str]


class MCPToolSchema(NamedTuple):
    """The full schema for a tool in the MCP format."""

    name: str
    description: str
    inputSchema: MCPInputSchema


class QueryResultDict(TypedDict, total=False):
    """The structured result of a graph query tool call."""

    query_used: str
    results: list[ResultRow]
    summary: str
    error: str


class CodeSnippetResultDict(TypedDict, total=False):
    """The structured result of a tool that retrieves a code snippet."""

    qualified_name: str
    source_code: str
    file_path: str
    line_start: int
    line_end: int
    docstring: str | None
    found: bool
    error_message: str | None
    error: str


class ListProjectsSuccessResult(TypedDict):
    """The successful result of listing projects."""

    projects: list[str]
    count: int


class ListProjectsErrorResult(TypedDict):
    """The error result of listing projects."""

    projects: list[str]
    count: int
    error: str


ListProjectsResult = ListProjectsSuccessResult | ListProjectsErrorResult
"""The result of the 'list_projects' tool."""


class DeleteProjectSuccessResult(TypedDict):
    """The successful result of deleting a project."""

    success: bool
    project: str
    message: str


class DeleteProjectErrorResult(TypedDict):
    """The error result of deleting a project."""

    success: bool
    error: str


DeleteProjectResult = DeleteProjectSuccessResult | DeleteProjectErrorResult
"""The result of the 'delete_project' tool."""


MCPResultType = (
    str
    | QueryResultDict
    | CodeSnippetResultDict
    | ListProjectsResult
    | DeleteProjectResult
)
"""A union of all possible result types from MCP tool handlers."""

MCPHandlerType = Callable[..., Awaitable[MCPResultType]]
"""The signature for an MCP tool handler function."""


# Graph schema definitions
class NodeSchema(NamedTuple):
    """Defines the schema for a type of node in the graph."""

    label: NodeLabel
    properties: str


class RelationshipSchema(NamedTuple):
    """Defines the schema for a type of relationship in the graph."""

    sources: tuple[NodeLabel, ...]
    rel_type: RelationshipType
    targets: tuple[NodeLabel, ...]


NODE_SCHEMAS: tuple[NodeSchema, ...] = (
    NodeSchema(NodeLabel.PROJECT, "{name: string}"),
    NodeSchema(
        NodeLabel.PACKAGE, "{qualified_name: string, name: string, path: string}"
    ),
    NodeSchema(NodeLabel.FOLDER, "{path: string, name: string}"),
    NodeSchema(NodeLabel.FILE, "{path: string, name: string, extension: string}"),
    NodeSchema(
        NodeLabel.MODULE, "{qualified_name: string, name: string, path: string}"
    ),
    NodeSchema(
        NodeLabel.CLASS,
        "{qualified_name: string, name: string, decorators: list[string]}",
    ),
    NodeSchema(
        NodeLabel.FUNCTION,
        "{qualified_name: string, name: string, decorators: list[string]}",
    ),
    NodeSchema(
        NodeLabel.METHOD,
        "{qualified_name: string, name: string, decorators: list[string]}",
    ),
    NodeSchema(NodeLabel.INTERFACE, "{qualified_name: string, name: string}"),
    NodeSchema(NodeLabel.ENUM, "{qualified_name: string, name: string}"),
    NodeSchema(NodeLabel.TYPE, "{qualified_name: string, name: string}"),
    NodeSchema(NodeLabel.UNION, "{qualified_name: string, name: string}"),
    NodeSchema(
        NodeLabel.MODULE_INTERFACE,
        "{qualified_name: string, name: string, path: string}",
    ),
    NodeSchema(
        NodeLabel.MODULE_IMPLEMENTATION,
        "{qualified_name: string, name: string, path: string, implements_module: string}",
    ),
    NodeSchema(NodeLabel.EXTERNAL_PACKAGE, "{name: string, version_spec: string}"),
)


RELATIONSHIP_SCHEMAS: tuple[RelationshipSchema, ...] = (
    RelationshipSchema(
        (NodeLabel.PROJECT, NodeLabel.PACKAGE, NodeLabel.FOLDER),
        RelationshipType.CONTAINS_PACKAGE,
        (NodeLabel.PACKAGE,),
    ),
    RelationshipSchema(
        (NodeLabel.PROJECT, NodeLabel.PACKAGE, NodeLabel.FOLDER),
        RelationshipType.CONTAINS_FOLDER,
        (NodeLabel.FOLDER,),
    ),
    RelationshipSchema(
        (NodeLabel.PROJECT, NodeLabel.PACKAGE, NodeLabel.FOLDER),
        RelationshipType.CONTAINS_FILE,
        (NodeLabel.FILE,),
    ),
    RelationshipSchema(
        (NodeLabel.PROJECT, NodeLabel.PACKAGE, NodeLabel.FOLDER),
        RelationshipType.CONTAINS_MODULE,
        (NodeLabel.MODULE,),
    ),
    RelationshipSchema(
        (NodeLabel.MODULE,),
        RelationshipType.DEFINES,
        (NodeLabel.CLASS, NodeLabel.FUNCTION),
    ),
    RelationshipSchema(
        (NodeLabel.CLASS,),
        RelationshipType.DEFINES_METHOD,
        (NodeLabel.METHOD,),
    ),
    RelationshipSchema(
        (NodeLabel.MODULE,),
        RelationshipType.IMPORTS,
        (NodeLabel.MODULE,),
    ),
    RelationshipSchema(
        (NodeLabel.MODULE,),
        RelationshipType.EXPORTS,
        (NodeLabel.CLASS, NodeLabel.FUNCTION),
    ),
    RelationshipSchema(
        (NodeLabel.MODULE,),
        RelationshipType.EXPORTS_MODULE,
        (NodeLabel.MODULE_INTERFACE,),
    ),
    RelationshipSchema(
        (NodeLabel.MODULE,),
        RelationshipType.IMPLEMENTS_MODULE,
        (NodeLabel.MODULE_IMPLEMENTATION,),
    ),
    RelationshipSchema(
        (NodeLabel.CLASS,),
        RelationshipType.INHERITS,
        (NodeLabel.CLASS,),
    ),
    RelationshipSchema(
        (NodeLabel.CLASS,),
        RelationshipType.IMPLEMENTS,
        (NodeLabel.INTERFACE,),
    ),
    RelationshipSchema(
        (NodeLabel.METHOD,),
        RelationshipType.OVERRIDES,
        (NodeLabel.METHOD,),
    ),
    RelationshipSchema(
        (NodeLabel.MODULE_IMPLEMENTATION,),
        RelationshipType.IMPLEMENTS,
        (NodeLabel.MODULE_INTERFACE,),
    ),
    RelationshipSchema(
        (NodeLabel.PROJECT,),
        RelationshipType.DEPENDS_ON_EXTERNAL,
        (NodeLabel.EXTERNAL_PACKAGE,),
    ),
    RelationshipSchema(
        (NodeLabel.FUNCTION, NodeLabel.METHOD),
        RelationshipType.CALLS,
        (NodeLabel.FUNCTION, NodeLabel.METHOD),
    ),
)
