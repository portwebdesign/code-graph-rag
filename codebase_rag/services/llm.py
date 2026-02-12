"""
This module provides services for interacting with Large Language Models (LLMs).

It includes a `CypherGenerator` for converting natural language questions into
Cypher queries for the graph database, and a factory function `create_rag_orchestrator`
for building the main agent responsible for handling user queries by orchestrating
various tools (like semantic search and graph queries).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger
from pydantic_ai import Agent, DeferredToolRequests, Tool

from codebase_rag.ai.prompts import (
    CYPHER_SYSTEM_PROMPT,
    LOCAL_CYPHER_SYSTEM_PROMPT,
    build_rag_orchestrator_prompt,
)
from codebase_rag.core.config import ModelConfig, settings

from ..core import constants as cs
from ..core import logs as ls
from ..infrastructure import exceptions as ex
from ..providers.base import get_provider_from_config

if TYPE_CHECKING:
    from pydantic_ai.models import Model


def _create_provider_model(config: ModelConfig) -> Model:
    """
    Creates a Pydantic-AI Model instance from a given model configuration.

    This helper function abstracts the process of initializing a model provider
    (like OpenAI, Ollama, etc.) and creating a model instance that can be used
    by an `Agent`.

    Args:
        config (ModelConfig): The configuration specifying the provider and model details.

    Returns:
        An initialized Pydantic-AI `Model` instance ready for use.
    """
    provider = get_provider_from_config(config)
    return provider.create_model(config.model_id)


def _clean_cypher_response(response_text: str) -> str:
    """
    Cleans and formats the raw text response from an LLM into a valid Cypher query.

    This function removes common LLM artifacts like backticks, "cypher" prefixes,
    and ensures the query ends with a semicolon.

    Args:
        response_text (str): The raw text output from the LLM.

    Returns:
        A cleaned, valid Cypher query string.
    """
    query = response_text.strip().replace(cs.CYPHER_BACKTICK, "")
    if query.startswith(cs.CYPHER_PREFIX):
        query = query[len(cs.CYPHER_PREFIX) :].strip()
    if not query.endswith(cs.CYPHER_SEMICOLON):
        query += cs.CYPHER_SEMICOLON
    return query


class CypherGenerator:
    """
    A service that uses an LLM agent to generate Cypher queries from natural language.

    This class encapsulates an agent specifically trained (via a system prompt) to
    translate a user's question about the codebase into a Cypher query that can be
    executed against the Neo4j graph database.
    """

    def __init__(self) -> None:
        """
        Initializes the CypherGenerator agent.

        It configures the agent with the appropriate model and system prompt based
        on the application settings.

        Raises:
            ex.LLMGenerationError: If the agent fails to initialize, for instance,
                                   due to incorrect API keys or model configuration.
        """
        try:
            config = settings.active_cypher_config
            llm = _create_provider_model(config)

            system_prompt = (
                LOCAL_CYPHER_SYSTEM_PROMPT
                if config.provider == cs.Provider.OLLAMA
                else CYPHER_SYSTEM_PROMPT
            )

            self.agent = Agent(
                model=llm,
                system_prompt=system_prompt,
                output_type=str,
                retries=settings.AGENT_RETRIES,
            )
        except Exception as e:
            raise ex.LLMGenerationError(ex.LLM_INIT_CYPHER.format(error=e)) from e

    async def generate(self, natural_language_query: str) -> str:
        """
        Generates a Cypher query from a natural language input.

        Args:
            natural_language_query (str): The user's question in natural language
                                          (e.g., "find all functions that call 'process_data'").

        Returns:
            The generated Cypher query as a string.

        Raises:
            ex.LLMGenerationError: If the LLM fails to generate a valid or coherent query.
        """
        logger.info(ls.CYPHER_GENERATING.format(query=natural_language_query))
        try:
            result = await self.agent.run(natural_language_query)
            if (
                not isinstance(result.output, str)
                or cs.CYPHER_MATCH_KEYWORD not in result.output.upper()
            ):
                raise ex.LLMGenerationError(
                    ex.LLM_INVALID_QUERY.format(output=result.output)
                )

            query = _clean_cypher_response(result.output)
            logger.info(ls.CYPHER_GENERATED.format(query=query))
            return query
        except Exception as e:
            logger.error(ls.CYPHER_ERROR.format(error=e))
            raise ex.LLMGenerationError(ex.LLM_GENERATION_FAILED.format(error=e)) from e


def create_rag_orchestrator(tools: list[Tool]) -> Agent:
    """
    Creates and configures the main RAG (Retrieval-Augmented Generation) orchestrator agent.

    This agent is the central component of the query-answering system. It is provided
    with a set of tools (e.g., for semantic search, graph queries) and uses the LLM's
    reasoning capabilities to decide which tools to use to answer a user's question.

    Args:
        tools (list[Tool]): A list of tools (functions with descriptions) to be made
                            available to the agent.

    Returns:
        A configured Pydantic-AI `Agent` instance ready to process queries.

    Raises:
        ex.LLMGenerationError: If the agent fails to initialize.
    """
    try:
        config = settings.active_orchestrator_config
        llm = _create_provider_model(config)

        return Agent(
            model=llm,
            system_prompt=build_rag_orchestrator_prompt(tools),
            tools=tools,
            retries=settings.AGENT_RETRIES,
            output_retries=settings.ORCHESTRATOR_OUTPUT_RETRIES,
            output_type=[str, DeferredToolRequests],
        )
    except Exception as e:
        raise ex.LLMGenerationError(ex.LLM_INIT_ORCHESTRATOR.format(error=e)) from e
