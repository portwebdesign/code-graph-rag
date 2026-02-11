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
    from codebase_rag.parsers.factory import ProcessorFactory as GraphProcessorFactory
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
