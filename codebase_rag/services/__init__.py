from importlib import import_module

from .protocols import IngestorProtocol, QueryProtocol

__all__ = [
    "IngestorProtocol",
    "QueryProtocol",
    "FileProcessingService",
    "GitDeltaService",
    "ParsePreparationService",
    "ResolverPassService",
    "SemanticEmbeddingService",
    "PreScanService",
    "DeclarativeParserService",
    "CrossFileResolverAnalyticsService",
    "AnalysisRunnerService",
    "PerformanceProfileService",
    "GitDeltaHeadService",
    "GraphStateService",
    "GraphUpdateConfig",
    "GraphUpdateConfigService",
    "GraphUpdaterContext",
    "GraphUpdateOrchestrator",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "ResolverPassService": (".graph_update_post_services", "ResolverPassService"),
    "SemanticEmbeddingService": (
        ".graph_update_post_services",
        "SemanticEmbeddingService",
    ),
    "PreScanService": (".graph_update_post_services", "PreScanService"),
    "DeclarativeParserService": (
        ".graph_update_post_services",
        "DeclarativeParserService",
    ),
    "CrossFileResolverAnalyticsService": (
        ".graph_update_post_services",
        "CrossFileResolverAnalyticsService",
    ),
    "AnalysisRunnerService": (".graph_update_post_services", "AnalysisRunnerService"),
    "PerformanceProfileService": (
        ".graph_update_post_services",
        "PerformanceProfileService",
    ),
    "GitDeltaHeadService": (".graph_update_post_services", "GitDeltaHeadService"),
    "GraphStateService": (".graph_update_state_services", "GraphStateService"),
    "GraphUpdateConfig": (".graph_update_config_service", "GraphUpdateConfig"),
    "GraphUpdateConfigService": (
        ".graph_update_config_service",
        "GraphUpdateConfigService",
    ),
    "GraphUpdaterContext": (".graph_update_context", "GraphUpdaterContext"),
    "GraphUpdateOrchestrator": (
        ".graph_update_orchestrator",
        "GraphUpdateOrchestrator",
    ),
    "FileProcessingService": (".graph_update_services", "FileProcessingService"),
    "GitDeltaService": (".graph_update_services", "GitDeltaService"),
    "ParsePreparationService": (".graph_update_services", "ParsePreparationService"),
}


def __getattr__(name: str):
    if name in _LAZY_IMPORTS:
        module_name, attr = _LAZY_IMPORTS[name]
        module = import_module(module_name, package=__name__)
        value = getattr(module, attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__} has no attribute {name}")
