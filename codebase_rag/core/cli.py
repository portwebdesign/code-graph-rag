import asyncio
from pathlib import Path

import typer
from loguru import logger
from rich.panel import Panel
from rich.table import Table

from codebase_rag.core import cli_help as ch
from codebase_rag.core import constants as cs
from codebase_rag.core import logs as ls
from codebase_rag.graph_db.graph_updater import GraphUpdater
from codebase_rag.infrastructure.parser_loader import load_parsers
from codebase_rag.services.protobuf_service import ProtobufFileIngestor
from codebase_rag.tools.health_checker import HealthChecker
from codebase_rag.tools.language import cli as language_cli

from .config import load_cgrignore_patterns, settings
from .main import (
    app_context,
    connect_memgraph,
    export_graph_to_file,
    main_async,
    main_optimize_async,
    prompt_for_unignored_directories,
    style,
    update_model_settings,
)

app = typer.Typer(
    name="code-graph-rag",
    help=ch.APP_DESCRIPTION,
    no_args_is_help=True,
    add_completion=False,
)


def validate_models_early() -> None:
    """Validates that the active orchestrator and cypher models have valid API keys."""
    try:
        orchestrator_config = settings.active_orchestrator_config
        orchestrator_config.validate_api_key(cs.ModelRole.ORCHESTRATOR)

        cypher_config = settings.active_cypher_config
        cypher_config.validate_api_key(cs.ModelRole.CYPHER)
    except ValueError as e:
        app_context.console.print(style(str(e), cs.Color.RED))
        raise typer.Exit(1) from e


def _update_and_validate_models(orchestrator: str | None, cypher: str | None) -> None:
    """Updates model settings based on CLI arguments and validates them.

    Args:
        orchestrator (str | None): The orchestrator model string from the CLI.
        cypher (str | None): The cypher model string from the CLI.
    """
    try:
        update_model_settings(orchestrator, cypher)
    except ValueError as e:
        app_context.console.print(style(str(e), cs.Color.RED))
        raise typer.Exit(1) from e

    validate_models_early()


@app.callback()
def _global_options(
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Suppress non-essential output (progress messages, banners, informational logs).",
        is_eager=True,
    ),
) -> None:
    """
    Global CLI callback to handle common options like 'quiet' mode.

    Args:
        quiet (bool): If True, suppresses non-error output.
    """
    settings.QUIET = quiet
    if quiet:
        logger.remove()
        logger.add(lambda msg: app_context.console.print(msg, end=""), level="ERROR")


def _info(msg: str) -> None:
    """Wrapper to print messages to the console only if quiet mode is disabled.

    Args:
        msg (str): The message to print.
    """
    if not settings.QUIET:
        app_context.console.print(msg)


@app.command(help=ch.CMD_START)
def start(
    repo_path: str | None = typer.Option(
        None, "--repo-path", help=ch.HELP_REPO_PATH_RETRIEVAL
    ),
    update_graph: bool = typer.Option(
        False,
        "--update-graph",
        help=ch.HELP_UPDATE_GRAPH,
    ),
    clean: bool = typer.Option(
        False,
        "--clean",
        help=ch.HELP_CLEAN_DB,
    ),
    output: str | None = typer.Option(
        None,
        "-o",
        "--output",
        help=ch.HELP_OUTPUT_GRAPH,
    ),
    orchestrator: str | None = typer.Option(
        None,
        "--orchestrator",
        help=ch.HELP_ORCHESTRATOR,
    ),
    cypher: str | None = typer.Option(
        None,
        "--cypher",
        help=ch.HELP_CYPHER_MODEL,
    ),
    no_confirm: bool = typer.Option(
        False,
        "--no-confirm",
        help=ch.HELP_NO_CONFIRM,
    ),
    batch_size: int | None = typer.Option(
        None,
        "--batch-size",
        min=1,
        help=ch.HELP_BATCH_SIZE,
    ),
    exclude: list[str] | None = typer.Option(
        None,
        "--exclude",
        help=ch.HELP_EXCLUDE_PATTERNS,
    ),
    interactive_setup: bool = typer.Option(
        False,
        "--interactive-setup",
        help=ch.HELP_INTERACTIVE_SETUP,
    ),
) -> None:
    """
    Starts the main application workflow.

    Can update the graph for a repository or start the interactive chat/RAG session.

    Args:
        repo_path (str | None): Path to the repository (defaults to current target).
        update_graph (bool): Whether to parse and update the graph database.
        clean (bool): Whether to clean the database before updating.
        output (str | None): Optional path to export the graph after update.
        orchestrator (str | None): Override orchestrator model provider.
        cypher (str | None): Override cypher generation model provider.
        no_confirm (bool): Skip confirmation prompts.
        batch_size (int | None): Batch size for database operations.
        exclude (list[str] | None): List of patterns to exclude from parsing.
        interactive_setup (bool): Whether to run interactive setup for exclusions.
    """
    app_context.session.confirm_edits = not no_confirm

    target_repo_path = repo_path or settings.TARGET_REPO_PATH

    if output and not update_graph:
        app_context.console.print(
            style(cs.CLI_ERR_OUTPUT_REQUIRES_UPDATE, cs.Color.RED)
        )
        raise typer.Exit(1)

    _update_and_validate_models(orchestrator, cypher)

    effective_batch_size = settings.resolve_batch_size(batch_size)

    if update_graph:
        repo_to_update = Path(target_repo_path)
        _info(
            style(cs.CLI_MSG_UPDATING_GRAPH.format(path=repo_to_update), cs.Color.GREEN)
        )

        cgrignore = load_cgrignore_patterns(repo_to_update)
        cli_excludes = frozenset(exclude) if exclude else frozenset()
        exclude_paths = cli_excludes | cgrignore.exclude or None
        unignore_paths: frozenset[str] | None = None
        if interactive_setup:
            unignore_paths = prompt_for_unignored_directories(repo_to_update, exclude)
        else:
            _info(style(cs.CLI_MSG_AUTO_EXCLUDE, cs.Color.YELLOW))
            unignore_paths = cgrignore.unignore or None

        with connect_memgraph(effective_batch_size) as ingestor:
            if clean:
                _info(style(cs.CLI_MSG_CLEANING_DB, cs.Color.YELLOW))
                ingestor.clean_database()
            ingestor.ensure_constraints()

            parsers, queries = load_parsers()

            updater = GraphUpdater(
                ingestor,
                repo_to_update,
                parsers,
                queries,
                unignore_paths,
                exclude_paths,
            )
            updater.run()

            if output:
                _info(style(cs.CLI_MSG_EXPORTING_TO.format(path=output), cs.Color.CYAN))
                if not export_graph_to_file(ingestor, output):
                    raise typer.Exit(1)

        _info(style(cs.CLI_MSG_GRAPH_UPDATED, cs.Color.GREEN))
        return

    try:
        asyncio.run(main_async(target_repo_path, effective_batch_size))
    except KeyboardInterrupt:
        app_context.console.print(style(cs.CLI_MSG_APP_TERMINATED, cs.Color.RED))
    except ValueError as e:
        app_context.console.print(
            style(cs.CLI_ERR_STARTUP.format(error=e), cs.Color.RED)
        )


@app.command(help=ch.CMD_INDEX)
def index(
    repo_path: str | None = typer.Option(
        None, "--repo-path", help=ch.HELP_REPO_PATH_INDEX
    ),
    output_proto_dir: str = typer.Option(
        ...,
        "-o",
        "--output-proto-dir",
        help=ch.HELP_OUTPUT_PROTO_DIR,
    ),
    split_index: bool = typer.Option(
        False,
        "--split-index",
        help=ch.HELP_SPLIT_INDEX,
    ),
    exclude: list[str] | None = typer.Option(
        None,
        "--exclude",
        help=ch.HELP_EXCLUDE_PATTERNS,
    ),
    interactive_setup: bool = typer.Option(
        False,
        "--interactive-setup",
        help=ch.HELP_INTERACTIVE_SETUP,
    ),
) -> None:
    """
    Indexes the repository and generates a Protobuf representation.

    Args:
        repo_path (str | None): Path to the repository to index.
        output_proto_dir (str): Directory to save the generated protobuf files.
        split_index (bool): Whether to split the index into multiple files.
        exclude (list[str] | None): List of patterns to exclude.
        interactive_setup (bool): Whether to run interactive setup.
    """
    target_repo_path = repo_path or settings.TARGET_REPO_PATH
    repo_to_index = Path(target_repo_path)
    _info(style(cs.CLI_MSG_INDEXING_AT.format(path=repo_to_index), cs.Color.GREEN))

    _info(style(cs.CLI_MSG_OUTPUT_TO.format(path=output_proto_dir), cs.Color.CYAN))

    cgrignore = load_cgrignore_patterns(repo_to_index)
    cli_excludes = frozenset(exclude) if exclude else frozenset()
    exclude_paths = cli_excludes | cgrignore.exclude or None
    unignore_paths: frozenset[str] | None = None
    if interactive_setup:
        unignore_paths = prompt_for_unignored_directories(repo_to_index, exclude)
    else:
        _info(style(cs.CLI_MSG_AUTO_EXCLUDE, cs.Color.YELLOW))
        unignore_paths = cgrignore.unignore or None

    try:
        ingestor = ProtobufFileIngestor(
            output_path=output_proto_dir, split_index=split_index
        )
        parsers, queries = load_parsers()
        updater = GraphUpdater(
            ingestor, repo_to_index, parsers, queries, unignore_paths, exclude_paths
        )

        updater.run()
        _info(style(cs.CLI_MSG_INDEXING_DONE, cs.Color.GREEN))

    except Exception as e:
        app_context.console.print(
            style(cs.CLI_ERR_INDEXING.format(error=e), cs.Color.RED)
        )
        logger.exception(ls.INDEXING_FAILED)
        raise typer.Exit(1) from e


@app.command(help=ch.CMD_EXPORT)
def export(
    output: str = typer.Option(..., "-o", "--output", help=ch.HELP_OUTPUT_PATH),
    format_json: bool = typer.Option(
        True, "--json/--no-json", help=ch.HELP_FORMAT_JSON
    ),
    batch_size: int | None = typer.Option(
        None,
        "--batch-size",
        min=1,
        help=ch.HELP_BATCH_SIZE,
    ),
) -> None:
    """
    Exports the current graph database to a file.

    Args:
        output (str): Output file path.
        format_json (bool): Whether to export in JSON format (currently the only supported format).
        batch_size (int | None): Batch size for fetching data during export.
    """
    if not format_json:
        app_context.console.print(style(cs.CLI_ERR_ONLY_JSON, cs.Color.RED))
        raise typer.Exit(1)

    _info(style(cs.CLI_MSG_CONNECTING_MEMGRAPH, cs.Color.CYAN))

    effective_batch_size = settings.resolve_batch_size(batch_size)

    try:
        with connect_memgraph(effective_batch_size) as ingestor:
            _info(style(cs.CLI_MSG_EXPORTING_DATA, cs.Color.CYAN))

            if not export_graph_to_file(ingestor, output):
                raise typer.Exit(1)

    except Exception as e:
        app_context.console.print(
            style(cs.CLI_ERR_EXPORT_FAILED.format(error=e), cs.Color.RED)
        )
        logger.exception(ls.EXPORT_ERROR.format(error=e))
        raise typer.Exit(1) from e


@app.command(help=ch.CMD_OPTIMIZE)
def optimize(
    language: str = typer.Argument(
        ...,
        help=ch.HELP_LANGUAGE_ARG,
    ),
    repo_path: str | None = typer.Option(
        None, "--repo-path", help=ch.HELP_REPO_PATH_OPTIMIZE
    ),
    reference_document: str | None = typer.Option(
        None,
        "--reference-document",
        help=ch.HELP_REFERENCE_DOC,
    ),
    orchestrator: str | None = typer.Option(
        None,
        "--orchestrator",
        help=ch.HELP_ORCHESTRATOR,
    ),
    cypher: str | None = typer.Option(
        None,
        "--cypher",
        help=ch.HELP_CYPHER_MODEL,
    ),
    no_confirm: bool = typer.Option(
        False,
        "--no-confirm",
        help=ch.HELP_NO_CONFIRM,
    ),
    batch_size: int | None = typer.Option(
        None,
        "--batch-size",
        min=1,
        help=ch.HELP_BATCH_SIZE,
    ),
) -> None:
    """
    Runs optimization analytics or transformations on the code.

    Args:
        language (str): Target programming language for optimization.
        repo_path (str | None): Path to the repository.
        reference_document (str | None): Path to a reference document/guideline.
        orchestrator (str | None): Override orchestrator model.
        cypher (str | None): Override cypher model.
        no_confirm (bool): Skip confirmations.
        batch_size (int | None): Database batch size.
    """
    app_context.session.confirm_edits = not no_confirm

    target_repo_path = repo_path or settings.TARGET_REPO_PATH

    _update_and_validate_models(orchestrator, cypher)

    try:
        asyncio.run(
            main_optimize_async(
                language,
                target_repo_path,
                reference_document,
                orchestrator,
                cypher,
                batch_size,
            )
        )
    except KeyboardInterrupt:
        app_context.console.print(style(cs.CLI_MSG_APP_TERMINATED, cs.Color.RED))
    except ValueError as e:
        app_context.console.print(
            style(cs.CLI_ERR_STARTUP.format(error=e), cs.Color.RED)
        )


@app.command(name=ch.CLICommandName.MCP_SERVER, help=ch.CMD_MCP_SERVER)
def mcp_server() -> None:
    """
    Starts the Model Context Protocol (MCP) server.

    Allows integration with IDEs and other tools that support MCP.
    """
    try:
        from codebase_rag.mcp import main as mcp_main

        asyncio.run(mcp_main())
    except KeyboardInterrupt:
        app_context.console.print(style(cs.CLI_MSG_APP_TERMINATED, cs.Color.RED))
    except ValueError as e:
        app_context.console.print(
            style(cs.CLI_ERR_CONFIG.format(error=e), cs.Color.RED)
        )
        _info(style(cs.CLI_MSG_HINT_TARGET_REPO, cs.Color.YELLOW))

    except Exception as e:
        app_context.console.print(
            style(cs.CLI_ERR_MCP_SERVER.format(error=e), cs.Color.RED)
        )


@app.command(name=ch.CLICommandName.GRAPH_LOADER, help=ch.CMD_GRAPH_LOADER)
def graph_loader_command(
    graph_file: str = typer.Argument(..., help=ch.HELP_GRAPH_FILE),
) -> None:
    """
    Loads a graph file and displays the summary.

    Args:
        graph_file (str): Path to the graph file to load.
    """
    from codebase_rag.graph_db.graph_loader import load_graph

    try:
        graph = load_graph(graph_file)
        summary = graph.summary()

        app_context.console.print(style(cs.CLI_MSG_GRAPH_SUMMARY, cs.Color.GREEN))
        app_context.console.print(f"  Total nodes: {summary['total_nodes']}")
        app_context.console.print(
            f"  Total relationships: {summary['total_relationships']}"
        )
        app_context.console.print(
            f"  Node types: {list(summary['node_labels'].keys())}"
        )
        app_context.console.print(
            f"  Relationship types: {list(summary['relationship_types'].keys())}"
        )
        app_context.console.print(
            f"  Exported at: {summary['metadata']['exported_at']}"
        )

    except Exception as e:
        app_context.console.print(
            style(cs.CLI_ERR_LOAD_GRAPH.format(error=e), cs.Color.RED)
        )
        raise typer.Exit(1) from e


@app.command(
    name=ch.CLICommandName.LANGUAGE,
    help=ch.CMD_LANGUAGE,
    context_settings={"allow_extra_args": True, "allow_interspersed_args": False},
)
def language_command(ctx: typer.Context) -> None:
    """
    Sub-command for language-specific operations.

    Delegates to the language tool CLI.

    Args:
        ctx (typer.Context): The Typer context, containing extra arguments.
    """
    language_cli(ctx.args, standalone_mode=False)


@app.command(name=ch.CLICommandName.DOCTOR, help=ch.CMD_DOCTOR)
def doctor() -> None:
    """
    Runs health checks for the environment and configuration.

    Verifies dependencies, connections, and required settings.
    """
    checker = HealthChecker()
    results = checker.run_all_checks()

    passed, total = checker.get_summary()

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="cyan", no_wrap=False)

    for result in results:
        status = "✓" if result.passed else "✗"
        status_color = cs.Color.GREEN if result.passed else cs.Color.RED
        status_text = style(status, status_color, cs.StyleModifier.NONE)

        check_name = f"{status_text} {result.name}"
        table.add_row(check_name)

    panel = Panel(
        table,
        title="Health Check",
        border_style="dim",
        padding=(1, 2),
    )

    app_context.console.print(panel)

    app_context.console.print()
    summary_text = f"{passed}/{total} checks passed"
    if passed == total:
        app_context.console.print(style(summary_text, cs.Color.GREEN))
    else:
        app_context.console.print(style(summary_text, cs.Color.YELLOW))

    failed_checks = [r for r in results if not r.passed and r.error]
    if failed_checks:
        app_context.console.print()
        app_context.console.print(style("Failed checks details:", cs.Color.YELLOW))
        for result in failed_checks:
            error_msg = f"  {result.name}: {result.error}"
            app_context.console.print(
                style(error_msg, cs.Color.YELLOW, cs.StyleModifier.NONE)
            )

    if passed < total:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
