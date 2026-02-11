from __future__ import annotations

from dataclasses import dataclass

from loguru import logger


@dataclass
class CrossFileStats:
    """
    Statistics regarding cross-file dependencies.

    Attributes:
        total_modules (int): Total number of modules analyzed.
        total_edges (int): Total number of dependency edges found.
        top_imports (list[tuple[str, int]]): Top modules by number of imports.
        top_dependents (list[tuple[str, int]]): Top modules by number of dependents.
    """

    total_modules: int
    total_edges: int
    top_imports: list[tuple[str, int]]
    top_dependents: list[tuple[str, int]]


class CrossFileResolver:
    """
    Resolves dependencies between files based on import mappings.

    Args:
        import_mapping (dict[str, dict[str, str]]): Mapping of imports for each module.
    """

    def __init__(self, import_mapping: dict[str, dict[str, str]]) -> None:
        self.import_mapping = import_mapping

    def build_index(self) -> CrossFileStats:
        """
        Build an index of cross-file dependencies and calculate statistics.

        Returns:
            CrossFileStats: Statistics about the cross-file dependencies.
        """
        dependencies: dict[str, set[str]] = {}
        reverse_deps: dict[str, set[str]] = {}

        for module, imports in self.import_mapping.items():
            for _, target in imports.items():
                dependencies.setdefault(module, set()).add(target)
                reverse_deps.setdefault(target, set()).add(module)

        total_edges = sum(len(targets) for targets in dependencies.values())
        total_modules = len(self.import_mapping)

        top_imports = sorted(
            ((module, len(targets)) for module, targets in dependencies.items()),
            key=lambda item: item[1],
            reverse=True,
        )[:10]

        top_dependents = sorted(
            ((module, len(sources)) for module, sources in reverse_deps.items()),
            key=lambda item: item[1],
            reverse=True,
        )[:10]

        return CrossFileStats(
            total_modules=total_modules,
            total_edges=total_edges,
            top_imports=top_imports,
            top_dependents=top_dependents,
        )

    def log_summary(self) -> None:
        """
        Log a summary of cross-file dependencies.
        """
        stats = self.build_index()
        logger.info(
            "Cross-file resolver: modules={}, edges={}",
            stats.total_modules,
            stats.total_edges,
        )
        if stats.top_imports:
            logger.info("Top importers: {}", stats.top_imports)
        if stats.top_dependents:
            logger.info("Top dependents: {}", stats.top_dependents)
