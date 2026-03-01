from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TypedDict, Unpack

from dotenv import load_dotenv
from loguru import logger
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from codebase_rag.core import constants as cs
from codebase_rag.core import logs
from codebase_rag.data_models.types_defs import CgrignorePatterns, ModelConfigKwargs
from codebase_rag.infrastructure import exceptions as ex

load_dotenv()


class ApiKeyInfoEntry(TypedDict):
    """Defines the structure for API key information."""

    env_var: str
    url: str
    name: str


API_KEY_INFO: dict[str, ApiKeyInfoEntry] = {
    cs.Provider.OPENAI: {
        "env_var": "OPENAI_API_KEY",
        "url": "https://platform.openai.com/api-keys",
        "name": "OpenAI",
    },
    cs.Provider.DEEPSEEK: {
        "env_var": "DEEPSEEK_API_KEY",
        "url": "https://platform.deepseek.com/",
        "name": "DeepSeek",
    },
    cs.Provider.ANTHROPIC: {
        "env_var": "ANTHROPIC_API_KEY",
        "url": "https://console.anthropic.com/settings/keys",
        "name": "Anthropic",
    },
    cs.Provider.GOOGLE: {
        "env_var": "GOOGLE_API_KEY",
        "url": "https://console.cloud.google.com/apis/credentials",
        "name": "Google AI",
    },
    cs.Provider.AZURE: {
        "env_var": "AZURE_API_KEY",
        "url": "https://portal.azure.com/",
        "name": "Azure OpenAI",
    },
    cs.Provider.COHERE: {
        "env_var": "COHERE_API_KEY",
        "url": "https://dashboard.cohere.com/api-keys",
        "name": "Cohere",
    },
}


def format_missing_api_key_errors(
    provider: str, role: str = cs.DEFAULT_MODEL_ROLE
) -> str:
    """Formats a user-friendly error message for a missing API key.

    Args:
        provider (str): The name of the LLM provider (e.g., 'openai').
        role (str): The role the model is serving (e.g., 'orchestrator').

    Returns:
        str: A formatted, helpful error message string.
    """
    provider_lower = provider.lower()

    if provider_lower in API_KEY_INFO:
        info = API_KEY_INFO[provider_lower]
        env_var = info["env_var"]
        url = info["url"]
        name = info["name"]
    else:
        env_var = f"{provider.upper()}_API_KEY"
        url = f"your {provider} provider's website"
        name = provider.capitalize()

    role_msg = f" for {role}" if role != cs.DEFAULT_MODEL_ROLE else ""

    error_msg = f"""
─── API Key Missing ───────────────────────────────────────────────

  Error: {env_var} environment variable is not set.
         This is required to use {name}{role_msg}.

  To fix this:

  1. Get your API key from:
     {url}

  2. Set it in your environment:
     export {env_var}='your-key-here'

     Or add it to your .env file in the project root:
     {env_var}=your-key-here

  3. Alternatively, you can use a local model with Ollama:
     (No API key required)

───────────────────────────────────────────────────────────────────
""".strip()  # noqa: W293
    return error_msg


@dataclass
class ModelConfig:
    """Represents the configuration for a specific language model.

    Attributes:
        provider (str): The provider of the model (e.g., 'openai', 'ollama').
        model_id (str): The specific model identifier (e.g., 'gpt-4', 'llama3').
        api_key (str | None): The API key for the provider's service.
        endpoint (str | None): The API endpoint URL.
        project_id (str | None): The project ID for providers like Google.
        region (str | None): The cloud region for the service.
        provider_type (str | None): The type of provider, e.g., for Azure.
        thinking_budget (int | None): A budget for model 'thinking' steps.
        service_account_file (str | None): Path to a service account file.
    """

    provider: str
    model_id: str
    api_key: str | None = None
    endpoint: str | None = None
    project_id: str | None = None
    region: str | None = None
    provider_type: str | None = None
    thinking_budget: int | None = None
    service_account_file: str | None = None

    def to_update_kwargs(self) -> ModelConfigKwargs:
        """Converts the config to a dictionary suitable for updating settings.

        Returns:
            ModelConfigKwargs: A TypedDict of the model's optional parameters.
        """
        result = asdict(self)
        del result[cs.FIELD_PROVIDER]
        del result[cs.FIELD_MODEL_ID]
        return ModelConfigKwargs(**result)

    def validate_api_key(self, role: str = cs.DEFAULT_MODEL_ROLE) -> None:
        """Validates that the API key is present for non-local providers.

        Args:
            role (str): The role the model is serving, for error messaging.

        Raises:
            ValueError: If the API key is missing for a required provider.
        """
        local_providers = {cs.Provider.OLLAMA, cs.Provider.LOCAL, cs.Provider.VLLM}
        if self.provider.lower() in local_providers:
            return
        if self.provider_type == cs.GoogleProviderType.VERTEX:
            return
        if (
            not self.api_key
            or not self.api_key.strip()
            or self.api_key == cs.DEFAULT_API_KEY
        ):
            error_msg = format_missing_api_key_errors(self.provider, role)
            logger.warning(error_msg)
            return


class AppConfig(BaseSettings):
    """Main application settings, loaded from environment variables or a .env file.

    This class uses Pydantic's `BaseSettings` to automatically load and validate
    configuration from the environment.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    MEMGRAPH_HOST: str = "localhost"
    MEMGRAPH_PORT: int = 7687
    MEMGRAPH_HTTP_PORT: int = 7444
    MEMGRAPH_USERNAME: str | None = None
    MEMGRAPH_PASSWORD: str | None = None
    LAB_PORT: int = 3000
    MEMGRAPH_BATCH_SIZE: int = 1000

    CODEGRAPH_MAGE_CYCLES: bool = True
    CODEGRAPH_CYCLE_LIMIT: int = 100
    CODEGRAPH_CYCLE_MIN_SIZE: int = 2

    AGENT_RETRIES: int = 3
    AGENT_MAX_STEPS: int = 6
    ORCHESTRATOR_OUTPUT_RETRIES: int = 100
    MCP_AGENT_TIMEOUT_SECONDS: int = 300

    MCP_SYNC_GRAPH_TIMEOUT_SECONDS: int = 900
    MCP_ORCHESTRATE_RETRY_BASE_DELAY_SECONDS: float = 0.05
    MCP_ORCHESTRATE_SYNC_RETRY_ATTEMPTS: int = 3
    MCP_ORCHESTRATE_VALIDATE_RETRY_ATTEMPTS: int = 2
    MCP_ORCHESTRATE_AUTO_NEXT_RETRY_ATTEMPTS: int = 2
    MCP_ORCHESTRATE_CB_FAILURE_THRESHOLD: int = 3
    MCP_ORCHESTRATE_CB_COOLDOWN_SECONDS: float = 10.0
    MCP_ORCHESTRATE_DEBOUNCE_DEFAULT_SECONDS: int = 2
    MCP_ORCHESTRATE_AUTO_EXECUTE_NEXT_DEFAULT: bool = True
    MCP_ORCHESTRATE_VERIFY_DRIFT_DEFAULT: bool = True

    REALTIME_WATCHER_DEBOUNCE_SECONDS: float = 2.0

    ORCHESTRATOR_PROVIDER: str = ""
    ORCHESTRATOR_MODEL: str = ""
    ORCHESTRATOR_API_KEY: str | None = None
    ORCHESTRATOR_ENDPOINT: str | None = None
    ORCHESTRATOR_PROJECT_ID: str | None = None
    ORCHESTRATOR_REGION: str = cs.DEFAULT_REGION
    ORCHESTRATOR_PROVIDER_TYPE: str | None = None
    ORCHESTRATOR_THINKING_BUDGET: int | None = None
    ORCHESTRATOR_SERVICE_ACCOUNT_FILE: str | None = None

    CYPHER_PROVIDER: str = ""
    CYPHER_MODEL: str = ""
    CYPHER_API_KEY: str | None = None
    CYPHER_ENDPOINT: str | None = None
    CYPHER_PROJECT_ID: str | None = None
    CYPHER_REGION: str = cs.DEFAULT_REGION
    CYPHER_PROVIDER_TYPE: str | None = None
    CYPHER_THINKING_BUDGET: int | None = None
    CYPHER_SERVICE_ACCOUNT_FILE: str | None = None

    OLLAMA_BASE_URL: str = "http://localhost:11434"

    @property
    def ollama_endpoint(self) -> str:
        """Constructs the Ollama v1 API endpoint URL."""
        return f"{self.OLLAMA_BASE_URL.rstrip('/')}/v1"

    TARGET_REPO_PATH: str = ".."
    SHELL_COMMAND_TIMEOUT: int = 30
    SHELL_COMMAND_ALLOWLIST: frozenset[str] = frozenset(
        {
            "ls",
            "rg",
            "cat",
            "git",
            "echo",
            "pwd",
            "pytest",
            "mypy",
            "ruff",
            "uv",
            "find",
            "pre-commit",
            "rm",
            "cp",
            "mv",
            "mkdir",
            "rmdir",
            "wc",
            "head",
            "tail",
            "sort",
            "uniq",
            "cut",
            "tr",
            "xargs",
            "awk",
            "sed",
            "tee",
        }
    )
    SHELL_READ_ONLY_COMMANDS: frozenset[str] = frozenset(
        {
            "ls",
            "cat",
            "find",
            "pwd",
            "rg",
            "echo",
            "wc",
            "head",
            "tail",
            "sort",
            "uniq",
            "cut",
            "tr",
        }
    )
    SHELL_SAFE_GIT_SUBCOMMANDS: frozenset[str] = frozenset(
        {
            "status",
            "log",
            "diff",
            "show",
            "ls-files",
            "remote",
            "config",
            "branch",
        }
    )

    QDRANT_DB_PATH: str = "./.qdrant_code_embeddings"
    QDRANT_COLLECTION_NAME: str = "code_embeddings"
    QDRANT_VECTOR_DIM: int = 768
    QDRANT_TOP_K: int = 5
    EMBEDDING_MAX_LENGTH: int = 512
    EMBEDDING_CACHE_SIZE: int = 1024
    EMBEDDING_PROGRESS_INTERVAL: int = 10

    CONTEXT7_API_KEY: str | None = None
    CONTEXT7_API_URL: str | None = None
    CONTEXT7_MCP_URL: str | None = None
    CONTEXT7_AUTO_ENABLED: bool = True
    CONTEXT7_AUTO_LIBRARIES: str = (
        "django,fastapi,flask,react,next.js,nextjs,express,nestjs,spring,rails,laravel"
    )
    CONTEXT7_PERSIST_GRAPH: bool = True
    CONTEXT7_PERSIST_MEMORY: bool = True
    CONTEXT7_DOC_TTL_DAYS: int = 30
    CONTEXT7_MAX_CHUNKS: int = 6
    CONTEXT7_MEMORY_MAX_CHARS: int = 1200

    CACHE_MAX_ENTRIES: int = 1000
    CACHE_MAX_MEMORY_MB: int = 500
    CACHE_EVICTION_DIVISOR: int = 10
    CACHE_MEMORY_THRESHOLD_RATIO: float = 0.8

    OLLAMA_HEALTH_TIMEOUT: float = 5.0

    _active_orchestrator: ModelConfig | None = None
    _active_cypher: ModelConfig | None = None

    QUIET: bool = Field(False, validation_alias="CGR_QUIET")

    def _get_default_config(self, role: str) -> ModelConfig:
        """Constructs a default model configuration for a given role from environment variables.

        If specific environment variables for the role are not set, it defaults to Ollama.

        Args:
            role (str): The role ('orchestrator' or 'cypher').

        Returns:
            ModelConfig: The constructed model configuration.
        """
        role_upper = role.upper()

        provider = getattr(self, f"{role_upper}_PROVIDER", None)
        model = getattr(self, f"{role_upper}_MODEL", None)

        if provider and model:
            return ModelConfig(
                provider=provider.lower(),
                model_id=model,
                api_key=getattr(self, f"{role_upper}_API_KEY", None),
                endpoint=getattr(self, f"{role_upper}_ENDPOINT", None),
                project_id=getattr(self, f"{role_upper}_PROJECT_ID", None),
                region=getattr(self, f"{role_upper}_REGION", cs.DEFAULT_REGION),
                provider_type=getattr(self, f"{role_upper}_PROVIDER_TYPE", None),
                thinking_budget=getattr(self, f"{role_upper}_THINKING_BUDGET", None),
                service_account_file=getattr(
                    self, f"{role_upper}_SERVICE_ACCOUNT_FILE", None
                ),
            )

        return ModelConfig(
            provider=cs.Provider.OLLAMA,
            model_id=cs.DEFAULT_MODEL,
            endpoint=self.ollama_endpoint,
            api_key=cs.DEFAULT_API_KEY,
        )

    def _get_default_orchestrator_config(self) -> ModelConfig:
        """Gets the default configuration for the orchestrator model."""
        return self._get_default_config(cs.ModelRole.ORCHESTRATOR)

    def _get_default_cypher_config(self) -> ModelConfig:
        """Gets the default configuration for the cypher model."""
        return self._get_default_config(cs.ModelRole.CYPHER)

    @property
    def active_orchestrator_config(self) -> ModelConfig:
        """Returns the currently active orchestrator model configuration."""
        return self._active_orchestrator or self._get_default_orchestrator_config()

    @property
    def active_cypher_config(self) -> ModelConfig:
        """Returns the currently active cypher model configuration."""
        return self._active_cypher or self._get_default_cypher_config()

    def set_orchestrator(
        self, provider: str, model: str, **kwargs: Unpack[ModelConfigKwargs]
    ) -> None:
        """Sets or overrides the orchestrator model configuration at runtime.

        Args:
            provider (str): The model provider.
            model (str): The model ID.
            **kwargs: Additional optional configuration parameters.
        """
        config = ModelConfig(provider=provider.lower(), model_id=model, **kwargs)
        self._active_orchestrator = config

    def set_cypher(
        self, provider: str, model: str, **kwargs: Unpack[ModelConfigKwargs]
    ) -> None:
        """Sets or overrides the cypher model configuration at runtime.

        Args:
            provider (str): The model provider.
            model (str): The model ID.
            **kwargs: Additional optional configuration parameters.
        """
        config = ModelConfig(provider=provider.lower(), model_id=model, **kwargs)
        self._active_cypher = config

    def parse_model_string(self, model_string: str) -> tuple[str, str]:
        """Parses a 'provider:model' string into a provider and model tuple.

        If no provider is specified, it defaults to 'ollama'.

        Args:
            model_string (str): The string to parse.

        Raises:
            ValueError: If the provider part is empty (e.g., ':model').

        Returns:
            tuple[str, str]: A tuple of (provider, model_id).
        """
        if ":" not in model_string:
            return cs.Provider.OLLAMA, model_string
        provider, model = model_string.split(":", 1)
        if not provider:
            raise ValueError(ex.PROVIDER_EMPTY)
        return provider.lower(), model

    def resolve_batch_size(self, batch_size: int | None) -> int:
        """Resolves the batch size to use for database operations.

        Uses the provided value, or falls back to the default from settings.

        Args:
            batch_size (int | None): The desired batch size.

        Raises:
            ValueError: If the resolved batch size is less than 1.

        Returns:
            int: The resolved batch size.
        """
        resolved = self.MEMGRAPH_BATCH_SIZE if batch_size is None else batch_size
        if resolved < 1:
            raise ValueError(ex.BATCH_SIZE_POSITIVE)
        return resolved


settings = AppConfig()

CGRIGNORE_FILENAME = ".cgrignore"


EMPTY_CGRIGNORE = CgrignorePatterns(exclude=frozenset(), unignore=frozenset())


def load_cgrignore_patterns(repo_path: Path) -> CgrignorePatterns:
    """Loads exclusion and inclusion patterns from a .cgrignore file.

    This function reads a file similar to .gitignore to determine which files
    and directories should be skipped or forcefully included during processing.

    Args:
        repo_path (Path): The root path of the repository where the .cgrignore
                          file is located.

    Returns:
        CgrignorePatterns: A dataclass containing frozensets of exclude and
                           unignore patterns. Returns empty sets if the file
                           doesn't exist or an error occurs.
    """
    ignore_file = repo_path / CGRIGNORE_FILENAME
    if not ignore_file.is_file():
        return EMPTY_CGRIGNORE

    exclude: set[str] = set()
    unignore: set[str] = set()
    try:
        with ignore_file.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("!"):
                    unignore.add(line[1:].strip())
                else:
                    exclude.add(line)
        if exclude or unignore:
            logger.info(
                logs.CGRIGNORE_LOADED.format(
                    exclude_count=len(exclude),
                    unignore_count=len(unignore),
                    path=ignore_file,
                )
            )
        return CgrignorePatterns(
            exclude=frozenset(exclude),
            unignore=frozenset(unignore),
        )
    except OSError as e:
        logger.warning(logs.CGRIGNORE_READ_FAILED.format(path=ignore_file, error=e))
        return EMPTY_CGRIGNORE
