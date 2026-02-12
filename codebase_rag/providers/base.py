from __future__ import annotations

import os
from abc import ABC, abstractmethod
from urllib.parse import urljoin

import httpx
from loguru import logger
from pydantic_ai.models.google import GoogleModel, GoogleModelSettings
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.google import GoogleProvider as PydanticGoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider as PydanticOpenAIProvider

from codebase_rag.core.config import ModelConfig, settings

from ..core import constants as cs
from ..core import logs as ls
from ..infrastructure import exceptions as ex


class ModelProvider(ABC):
    """Abstract base class for all language model providers."""

    def __init__(self, **config: str | int | None) -> None:
        """
        Initializes the provider with a given configuration.

        Args:
            **config: Arbitrary keyword arguments for provider-specific settings.
        """
        self.config = config

    @abstractmethod
    def create_model(
        self, model_id: str, **kwargs: str | int | None
    ) -> GoogleModel | OpenAIResponsesModel | OpenAIChatModel:
        """
        Creates an instance of a language model.

        Args:
            model_id (str): The identifier for the model to create.
            **kwargs: Additional keyword arguments for model creation.

        Returns:
            An instance of a Pydantic-AI model class.
        """
        pass

    @abstractmethod
    def validate_config(self) -> None:
        """
        Validates the provider's configuration, raising an error if invalid.
        """
        pass

    @property
    @abstractmethod
    def provider_name(self) -> cs.Provider:
        """
        Returns the name of the provider.

        Returns:
            cs.Provider: The provider's enum name.
        """
        pass


class GoogleProvider(ModelProvider):
    """Provider for Google's Gemini models (GLA and Vertex AI)."""

    def __init__(
        self,
        api_key: str | None = None,
        provider_type: cs.GoogleProviderType = cs.GoogleProviderType.GLA,
        project_id: str | None = None,
        region: str = cs.DEFAULT_REGION,
        service_account_file: str | None = None,
        thinking_budget: int | None = None,
        **kwargs: str | int | None,
    ) -> None:
        """
        Initializes the GoogleProvider.

        Args:
            api_key (str | None): The API key for Gemini GLA.
            provider_type (cs.GoogleProviderType): The type of Google provider ('gla' or 'vertex').
            project_id (str | None): The Google Cloud project ID for Vertex AI.
            region (str): The Google Cloud region for Vertex AI.
            service_account_file (str | None): Path to a service account JSON file for Vertex AI.
            thinking_budget (int | None): The thinking budget for the model.
            **kwargs: Additional keyword arguments.
        """
        super().__init__(**kwargs)
        self.api_key = api_key or os.environ.get(cs.ENV_GOOGLE_API_KEY)
        self.provider_type = provider_type
        self.project_id = project_id
        self.region = region
        self.service_account_file = service_account_file
        self.thinking_budget = thinking_budget

    @property
    def provider_name(self) -> cs.Provider:
        """Returns the provider name."""
        return cs.Provider.GOOGLE

    def validate_config(self) -> None:
        """Validates the configuration for the selected Google provider type."""
        if self.provider_type == cs.GoogleProviderType.GLA and not self.api_key:
            raise ValueError(ex.GOOGLE_GLA_NO_KEY)
        if self.provider_type == cs.GoogleProviderType.VERTEX and not self.project_id:
            raise ValueError(ex.GOOGLE_VERTEX_NO_PROJECT)

    def create_model(self, model_id: str, **kwargs: str | int | None) -> GoogleModel:
        """
        Creates a GoogleModel instance.

        Args:
            model_id (str): The ID of the Gemini model.
            **kwargs: Additional keyword arguments.

        Returns:
            GoogleModel: An initialized GoogleModel instance.
        """
        self.validate_config()

        if self.provider_type == cs.GoogleProviderType.VERTEX:
            credentials = None
            if self.service_account_file:
                # (H) Convert service account file to credentials object for pydantic-ai
                from google.oauth2 import service_account

                credentials = service_account.Credentials.from_service_account_file(
                    self.service_account_file,
                    scopes=[cs.GOOGLE_CLOUD_SCOPE],
                )
            provider = PydanticGoogleProvider(
                project=self.project_id,
                location=self.region,
                credentials=credentials,
            )
        else:
            # (H) api_key is guaranteed to be set by validate_config for gla type
            assert self.api_key is not None
            provider = PydanticGoogleProvider(api_key=self.api_key)

        if self.thinking_budget is None:
            return GoogleModel(model_id, provider=provider)
        model_settings = GoogleModelSettings(
            google_thinking_config={"thinking_budget": int(self.thinking_budget)}
        )
        return GoogleModel(model_id, provider=provider, settings=model_settings)


class OpenAIProvider(ModelProvider):
    """Provider for OpenAI-compatible APIs, including OpenAI itself."""

    def __init__(
        self,
        api_key: str | None = None,
        endpoint: str = cs.OPENAI_DEFAULT_ENDPOINT,
        **kwargs: str | int | None,
    ) -> None:
        """
        Initializes the OpenAIProvider.

        Args:
            api_key (str | None): The API key.
            endpoint (str): The API base URL.
            **kwargs: Additional keyword arguments.
        """
        super().__init__(**kwargs)
        self.api_key = api_key or os.environ.get(cs.ENV_OPENAI_API_KEY)
        self.endpoint = endpoint

    @property
    def provider_name(self) -> cs.Provider:
        """Returns the provider name."""
        return cs.Provider.OPENAI

    def validate_config(self) -> None:
        """Validates that the API key is set."""
        if not self.api_key:
            raise ValueError(ex.OPENAI_NO_KEY)

    def create_model(
        self, model_id: str, **kwargs: str | int | None
    ) -> OpenAIResponsesModel | OpenAIChatModel:
        """
        Creates an OpenAIResponsesModel instance.

        Args:
            model_id (str): The ID of the OpenAI model.
            **kwargs: Additional keyword arguments.

        Returns:
            OpenAIResponsesModel | OpenAIChatModel: An initialized OpenAI model instance.
        """
        self.validate_config()

        provider = PydanticOpenAIProvider(api_key=self.api_key, base_url=self.endpoint)
        return OpenAIResponsesModel(model_id, provider=provider)


class OllamaProvider(ModelProvider):
    """Provider for Ollama, which uses an OpenAI-compatible API."""

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str = cs.DEFAULT_API_KEY,
        **kwargs: str | int | None,
    ) -> None:
        """
        Initializes the OllamaProvider.

        Args:
            endpoint (str | None): The Ollama server endpoint URL.
            api_key (str): The API key (typically 'ollama' for Ollama).
            **kwargs: Additional keyword arguments.
        """
        super().__init__(**kwargs)
        self.endpoint = endpoint or settings.ollama_endpoint
        self.api_key = api_key

    @property
    def provider_name(self) -> cs.Provider:
        """Returns the provider name."""
        return cs.Provider.OLLAMA

    def validate_config(self) -> None:
        """Validates that the Ollama server is running and accessible."""
        base_url = self.endpoint.rstrip(cs.V1_PATH).rstrip("/")

        if not check_ollama_running(base_url):
            raise ValueError(ex.OLLAMA_NOT_RUNNING.format(endpoint=base_url))

    def create_model(
        self, model_id: str, **kwargs: str | int | None
    ) -> OpenAIChatModel:
        """
        Creates an OpenAIChatModel instance configured for Ollama.

        Args:
            model_id (str): The ID of the Ollama model.
            **kwargs: Additional keyword arguments.

        Returns:
            OpenAIChatModel: An initialized OpenAIChatModel instance.
        """
        self.validate_config()

        provider = PydanticOpenAIProvider(api_key=self.api_key, base_url=self.endpoint)
        return OpenAIChatModel(model_id, provider=provider)


class DeepSeekProvider(OpenAIProvider):
    """Provider for DeepSeek's OpenAI-compatible API."""

    def __init__(
        self,
        api_key: str | None = None,
        endpoint: str | None = None,
        **kwargs: str | int | None,
    ) -> None:
        resolved_endpoint = endpoint or cs.DEEPSEEK_DEFAULT_ENDPOINT
        super().__init__(api_key=api_key, endpoint=resolved_endpoint, **kwargs)

    @property
    def provider_name(self) -> cs.Provider:
        return cs.Provider.DEEPSEEK

    def create_model(
        self, model_id: str, **kwargs: str | int | None
    ) -> OpenAIChatModel:
        self.validate_config()
        provider = PydanticOpenAIProvider(api_key=self.api_key, base_url=self.endpoint)
        resolved_model = model_id
        allow_reasoner = bool(kwargs.get("allow_reasoner"))
        force_no_tools = bool(kwargs.get("force_no_tools"))
        if (
            model_id.lower() == "deepseek-reasoner"
            and not allow_reasoner
            and not force_no_tools
        ):
            resolved_model = "deepseek-chat"
            logger.warning(
                "DeepSeek reasoner requires reasoning_content for tool calls; using deepseek-chat instead."
            )
        return OpenAIChatModel(resolved_model, provider=provider)


PROVIDER_REGISTRY: dict[str, type[ModelProvider]] = {
    cs.Provider.GOOGLE: GoogleProvider,
    cs.Provider.OPENAI: OpenAIProvider,
    cs.Provider.OLLAMA: OllamaProvider,
    cs.Provider.DEEPSEEK: DeepSeekProvider,
}
"""A registry mapping provider names to their corresponding classes."""


def get_provider(
    provider_name: str | cs.Provider, **config: str | int | None
) -> ModelProvider:
    """
    Factory function to get a provider instance by name.

    Args:
        provider_name (str | cs.Provider): The name of the provider.
        **config: Configuration arguments for the provider.

    Returns:
        ModelProvider: An instance of the requested provider.
    """
    provider_key = str(provider_name)
    if provider_key not in PROVIDER_REGISTRY:
        available = ", ".join(PROVIDER_REGISTRY.keys())
        raise ValueError(
            ex.UNKNOWN_PROVIDER.format(provider=provider_name, available=available)
        )

    provider_class = PROVIDER_REGISTRY[provider_key]
    return provider_class(**config)


def get_provider_from_config(config: ModelConfig) -> ModelProvider:
    """
    Factory function to get a provider instance from a ModelConfig object.

    Args:
        config (ModelConfig): The model configuration object.

    Returns:
        ModelProvider: An instance of the provider specified in the config.
    """
    return get_provider(
        config.provider,
        api_key=config.api_key,
        endpoint=config.endpoint,
        project_id=config.project_id,
        region=config.region,
        provider_type=config.provider_type,
        thinking_budget=config.thinking_budget,
        service_account_file=config.service_account_file,
    )


def register_provider(name: str, provider_class: type[ModelProvider]) -> None:
    """
    Dynamically registers a new provider class.

    Args:
        name (str): The name to register the provider under.
        provider_class (type[ModelProvider]): The provider class to register.
    """
    PROVIDER_REGISTRY[name] = provider_class
    logger.info(ls.PROVIDER_REGISTERED.format(name=name))


def list_providers() -> list[str]:
    """
    Lists the names of all registered providers.

    Returns:
        list[str]: A list of provider names.
    """
    return list(PROVIDER_REGISTRY.keys())


def check_ollama_running(endpoint: str | None = None) -> bool:
    """
    Checks if the Ollama server is running and responsive.

    Args:
        endpoint (str | None): The base URL of the Ollama server.

    Returns:
        bool: True if the server is running, False otherwise.
    """
    endpoint = endpoint or settings.OLLAMA_BASE_URL
    try:
        health_url = urljoin(endpoint, cs.OLLAMA_HEALTH_PATH)
        with httpx.Client(timeout=settings.OLLAMA_HEALTH_TIMEOUT) as client:
            response = client.get(health_url)
            return response.status_code == cs.HTTP_OK
    except (httpx.RequestError, httpx.TimeoutException):
        return False
