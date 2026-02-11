from .api_compliance import ApiComplianceModule
from .base_module import AnalysisContext, AnalysisModule
from .complexity import ComplexityModule
from .dead_code import DeadCodeModule
from .dead_code_ai import DeadCodeAIModule
from .dependencies import DependenciesModule
from .dependency_health import DependencyHealthModule
from .documentation_quality import DocumentationQualityModule
from .framework_matcher import FrameworkMatcherModule
from .hotspots import HotspotsModule
from .migration import MigrationModule
from .ml_insights import MLInsightsModule
from .performance_analysis import PerformanceAnalysisModule
from .schema_validator import SchemaValidatorModule
from .security import SecurityModule

__all__ = [
    "AnalysisContext",
    "AnalysisModule",
    "ComplexityModule",
    "DeadCodeModule",
    "DeadCodeAIModule",
    "DependenciesModule",
    "FrameworkMatcherModule",
    "HotspotsModule",
    "MigrationModule",
    "MLInsightsModule",
    "SecurityModule",
    "SchemaValidatorModule",
    "PerformanceAnalysisModule",
    "DependencyHealthModule",
    "ApiComplianceModule",
    "DocumentationQualityModule",
]
