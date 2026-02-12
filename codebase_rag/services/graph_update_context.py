"""
This module defines the `GraphUpdaterContext`, a data class used to hold the
shared state and services required during the graph update process.

Using a context object like this allows for cleaner and more maintainable code by
avoiding the need to pass a large number of arguments between the different
services and processing steps involved in updating the code graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from codebase_rag.data_models.types_defs import (
    ASTCacheProtocol,
    FunctionRegistryTrieProtocol,
    SimpleNameLookup,
)

from .graph_update_config_service import GraphUpdateConfig

if TYPE_CHECKING:
    from codebase_rag.parsers.core.factory import (
        ProcessorFactory as GraphProcessorFactory,
    )
    from codebase_rag.services.graph_update_post_services import (
        CrossFileResolverAnalyticsService as CrossFileResolverService,
    )
    from codebase_rag.services.graph_update_post_services import (
        DeclarativeParserService,
    )
    from codebase_rag.services.graph_update_post_services import (
        ResolverPassService as ResolverService,
    )


@dataclass
class GraphUpdaterContext:
    """
    A data class that holds the shared state and services for the graph update process.

    This object acts as a dependency injection container, providing all necessary
    components to the various stages of the graph update pipeline.

    Attributes:
        ingestor (object): The service for writing data to the graph.
        repo_path (Path): The root path of the repository being processed.
        project_name (str): The name of the project.
        factory (GraphProcessorFactory): A factory for creating language-specific processors.
        ast_cache (ASTCacheProtocol): A cache for storing parsed Abstract Syntax Trees.
        function_registry (FunctionRegistryTrieProtocol): A registry of all found functions/classes.
        simple_name_lookup (SimpleNameLookup): A mapping from simple names to qualified names.
        queries (dict): A dictionary of pre-compiled tree-sitter queries.
        pre_scan_index (object | None): An optional index from a pre-scan pass for faster lookups.
        declarative_parser (object | None): An optional parser for declarative configuration files.
        config (GraphUpdateConfig): The configuration settings for the graph update.
        resolver_service (ResolverService): The service for the relationship resolution pass.
        declarative_parser_service (DeclarativeParserService): The service for parsing declarative files.
        cross_file_resolver_service (CrossFileResolverService): The service for analyzing cross-file dependencies.
    """

    ingestor: object
    repo_path: Path
    project_name: str
    factory: GraphProcessorFactory
    ast_cache: ASTCacheProtocol
    function_registry: FunctionRegistryTrieProtocol
    simple_name_lookup: SimpleNameLookup
    queries: dict
    pre_scan_index: object | None
    declarative_parser: object | None
    config: GraphUpdateConfig
    resolver_service: ResolverService
    declarative_parser_service: DeclarativeParserService
    cross_file_resolver_service: CrossFileResolverService
