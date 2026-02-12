from __future__ import annotations

from typing import TYPE_CHECKING, cast

from loguru import logger

from codebase_rag.core import logs as ls

from .graph_update_context import GraphUpdaterContext

if TYPE_CHECKING:
    from codebase_rag.parsers.query.declarative_parser import DeclarativeParser


class GraphUpdateOrchestrator:
    def __init__(self, context: GraphUpdaterContext) -> None:
        self.context = context

    def run_linking_and_passes(self) -> None:
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
