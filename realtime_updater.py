from __future__ import annotations

import sys
from typing import Annotated

import typer
from loguru import logger

from codebase_rag.core import cli_help as ch
from codebase_rag.core import logs
from codebase_rag.core.config import settings
from codebase_rag.core.constants import LOG_LEVEL_INFO, REALTIME_LOGGER_FORMAT
from codebase_rag.infrastructure import tool_errors as te
from codebase_rag.services.realtime_watcher import CodeChangeEventHandler, start_watcher

__all__ = ["CodeChangeEventHandler", "main", "start_watcher"]


def _validate_positive_int(value: int | None) -> int | None:
    if value is None:
        return None
    if value < 1:
        raise typer.BadParameter(te.INVALID_POSITIVE_INT.format(value=value))
    return value


def main(
    repo_path: Annotated[str, typer.Argument(help=ch.HELP_REPO_PATH_WATCH)],
    host: Annotated[
        str, typer.Option(help=ch.HELP_MEMGRAPH_HOST)
    ] = settings.MEMGRAPH_HOST,
    port: Annotated[
        int, typer.Option(help=ch.HELP_MEMGRAPH_PORT)
    ] = settings.MEMGRAPH_PORT,
    batch_size: Annotated[
        int | None,
        typer.Option(
            help=ch.HELP_BATCH_SIZE,
            callback=_validate_positive_int,
        ),
    ] = None,
    refresh_embeddings: Annotated[
        bool,
        typer.Option(
            "--refresh-embeddings",
            help="Regenerate semantic embeddings after each realtime graph update.",
        ),
    ] = False,
    debounce_seconds: Annotated[
        float,
        typer.Option(
            "--debounce-seconds",
            help="Debounce delay for frequent file events (recommended 2-5 seconds).",
        ),
    ] = settings.REALTIME_WATCHER_DEBOUNCE_SECONDS,
) -> None:
    logger.remove()
    logger.add(sys.stdout, format=REALTIME_LOGGER_FORMAT, level=LOG_LEVEL_INFO)
    logger.info(logs.LOGGER_CONFIGURED)
    start_watcher(
        repo_path,
        host,
        port,
        batch_size,
        refresh_embeddings=refresh_embeddings,
        debounce_seconds=debounce_seconds,
    )


if __name__ == "__main__":
    typer.run(main)
