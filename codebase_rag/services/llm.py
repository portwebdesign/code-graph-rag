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

    Args:
        config (ModelConfig): The configuration specifying the provider and model.

    Returns:
        Model: An initialized Pydantic-AI Model instance.
    """
    provider = get_provider_from_config(config)
    return provider.create_model(config.model_id)


def _clean_cypher_response(response_text: str) -> str:
    """
    Cleans and formats the raw text response from the LLM into a valid Cypher query.

    Args:
        response_text (str): The raw text output from the LLM.

    Returns:
        str: A cleaned, valid Cypher query string.
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
    """

    def __init__(self) -> None:
        """
        Initializes the CypherGenerator agent.

        Raises:
            ex.LLMGenerationError: If the agent fails to initialize.
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
            natural_language_query (str): The user's question in natural language.

        Returns:
            str: The generated Cypher query.

        Raises:
            ex.LLMGenerationError: If the LLM fails to generate a valid query.
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
    Creates and configures the main RAG orchestrator agent.

    Args:
        tools (list[Tool]): A list of tools to be made available to the agent.

    Returns:
        Agent: The configured Pydantic-AI Agent instance.

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
