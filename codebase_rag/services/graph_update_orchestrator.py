"""
This module defines the `GraphUpdateOrchestrator`, which is responsible for
running the various post-processing and linking passes after the initial
definition extraction is complete.

After all files have been parsed and their basic definitions (functions, classes)
have been ingested, this orchestrator runs a series of services in a specific
order to build the relationships between these definitions. This includes resolving
function calls, linking type hierarchies, processing framework-specific metadata,
and analyzing cross-file dependencies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from loguru import logger

from codebase_rag.core import logs as ls

from .graph_update_context import GraphUpdaterContext

if TYPE_CHECKING:
    from codebase_rag.parsers.query.declarative_parser import DeclarativeParser


class GraphUpdateOrchestrator:
    """
    Orchestrates the running of various linking and post-processing passes.

    This class takes the shared `GraphUpdaterContext` and calls the different
    services in the correct sequence to build the complete, interconnected code graph.
    """

    def __init__(self, context: GraphUpdaterContext) -> None:
        """
        Initializes the GraphUpdateOrchestrator.

        Args:
            context (GraphUpdaterContext): The shared context containing all necessary
                                           services and data for the update process.
        """
        self.context = context

    def run_linking_and_passes(self) -> None:
        """
        Executes all the linking and post-processing passes in sequence.

        This method is the main entry point for the orchestration logic. It logs
        the progress as it moves through each pass, from declarative parsing and
        framework linking to call resolution and type analysis.
        """
        ctx = self.context

        ctx.declarative_parser_service.run(
            cast("DeclarativeParser | None", ctx.declarative_parser),
            ctx.ast_cache,
            ctx.queries,
        )

        if ctx.config.framework_metadata_enabled:
            logger.info("Linking framework endpoints")
            ctx.resolver_service.process_framework_links(ctx.simple_name_lookup)

        if (
            ctx.config.tailwind_metadata_enabled
            or ctx.config.framework_metadata_enabled
        ):
            logger.info("Linking Tailwind usage")
            ctx.resolver_service.process_tailwind_usage(ctx.ast_cache)

        logger.info(ls.FOUND_FUNCTIONS.format(count=len(ctx.function_registry)))
        logger.info(ls.PASS_3_CALLS)
        ctx.resolver_service.process_function_calls(
            ctx.ast_cache, ctx.factory.call_processor, ctx.queries
        )

        if ctx.config.pass2_resolver_enabled or ctx.config.framework_metadata_enabled:
            logger.info("Running resolver pass 2")
            ctx.resolver_service.process_resolver_pass(ctx.ast_cache)

        ctx.cross_file_resolver_service.log_summary(
            ctx.factory.import_processor.import_mapping
        )

        logger.info("Running type relation pass")
        ctx.resolver_service.process_type_relations(ctx.ast_cache)

        logger.info("Running extended relation pass")
        ctx.resolver_service.process_extended_relations(ctx.ast_cache)

        if ctx.config.reparse_registry_enabled:
            logger.info("Running reparse registry resolver")
            ctx.resolver_service.process_reparse_registry(ctx.ast_cache)

        ctx.factory.definition_processor.process_all_method_overrides()

        logger.info("Running Context7 semantic bridging")
        ctx.resolver_service.process_context7_bridging()
