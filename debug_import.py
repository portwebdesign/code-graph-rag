from __future__ import annotations

from loguru import logger

try:
    from codebase_rag.parsers.query.query_engine import QueryEngine

    logger.info("Successfully imported QueryEngine: {}", QueryEngine)
except ImportError as e:
    logger.error("ImportError: {}", e)
except Exception as e:
    logger.exception("Error: {}", e)

try:
    logger.info("Successfully imported _QUERY_NAME_MAP")
except Exception as e:
    logger.exception("Error importing adapter: {}", e)
