import os
from unittest.mock import patch

import pytest

from codebase_rag.core.config import (
    DEFAULT_ENV_FILE,
    AppConfig,
    resolve_provider_api_key,
)


class TestProviderConfiguration:
    """Test provider-explicit configuration system."""

    def test_explicit_provider_configuration_from_env(self) -> None:
        """Test that explicit provider configuration from env vars works correctly."""
        with patch.dict(
            os.environ,
            {
                "ORCHESTRATOR_PROVIDER": "ollama",
                "ORCHESTRATOR_MODEL": "llama3.2",
                "ORCHESTRATOR_ENDPOINT": "http://localhost:11434/v1",
                "CYPHER_PROVIDER": "google",
                "CYPHER_MODEL": "gemini-2.5-flash",
                "CYPHER_API_KEY": "test-key",
            },
        ):
            config = AppConfig()

            orch_config = config.active_orchestrator_config
            assert orch_config.provider == "ollama"
            assert orch_config.model_id == "llama3.2"
            assert orch_config.endpoint == "http://localhost:11434/v1"

            cypher_config = config.active_cypher_config
            assert cypher_config.provider == "google"
            assert cypher_config.model_id == "gemini-2.5-flash"
            assert cypher_config.api_key == "test-key"

    def test_ollama_env_vars_respected_not_ignored(self) -> None:
        """
        Test that .env file with ORCHESTRATOR_PROVIDER=ollama is respected,
        not defaulting to other providers.
        """
        with patch.dict(
            os.environ,
            {
                "ORCHESTRATOR_PROVIDER": "ollama",
                "ORCHESTRATOR_MODEL": "llama3.2",
                "ORCHESTRATOR_ENDPOINT": "http://localhost:11434/v1",
                "CYPHER_PROVIDER": "ollama",
                "CYPHER_MODEL": "codellama",
                "CYPHER_ENDPOINT": "http://localhost:11434/v1",
            },
        ):
            config = AppConfig()

            orch_config = config.active_orchestrator_config
            assert orch_config.provider == "ollama", (
                "Should use Ollama from env vars, not default to other providers"
            )
            assert orch_config.model_id == "llama3.2"

            cypher_config = config.active_cypher_config
            assert cypher_config.provider == "ollama", (
                "Should use Ollama from env vars, not default to other providers"
            )
            assert cypher_config.model_id == "codellama"

    def test_custom_model_names_with_colons(self) -> None:
        """
        Test that custom model names with colons work correctly with provider:model format.
        """
        config = AppConfig()

        provider, model = config.parse_model_string("openai:gpt-oss:20b")
        assert provider == "openai"
        assert model == "gpt-oss:20b"

        provider, model = config.parse_model_string("ollama:custom-model:v2.1")
        assert provider == "ollama"
        assert model == "custom-model:v2.1"

        provider, model = config.parse_model_string("google:custom:model:v1.0")
        assert provider == "google"
        assert model == "custom:model:v1.0"

    def test_runtime_provider_override(self) -> None:
        """Test that runtime provider overrides work correctly."""
        config = AppConfig()

        config.set_orchestrator(
            "openai", "gpt-4o", api_key="test-key", endpoint="https://api.openai.com/v1"
        )

        orch_config = config.active_orchestrator_config
        assert orch_config.provider == "openai"
        assert orch_config.model_id == "gpt-4o"
        assert orch_config.api_key == "test-key"
        assert orch_config.endpoint == "https://api.openai.com/v1"

        config.set_cypher(
            "google", "gemini-2.5-flash", api_key="google-key", provider_type="gla"
        )

        cypher_config = config.active_cypher_config
        assert cypher_config.provider == "google"
        assert cypher_config.model_id == "gemini-2.5-flash"
        assert cypher_config.api_key == "google-key"
        assert cypher_config.provider_type == "gla"

    def test_mixed_provider_configuration(self) -> None:
        """Test that mixed provider configurations work (Google + Ollama, etc.)."""
        with patch.dict(
            os.environ,
            {
                "ORCHESTRATOR_PROVIDER": "google",
                "ORCHESTRATOR_MODEL": "gemini-2.5-pro",
                "ORCHESTRATOR_API_KEY": "google-key",
                "CYPHER_PROVIDER": "ollama",
                "CYPHER_MODEL": "codellama",
                "CYPHER_ENDPOINT": "http://localhost:11434/v1",
            },
        ):
            config = AppConfig()

            orch_config = config.active_orchestrator_config
            assert orch_config.provider == "google"
            assert orch_config.model_id == "gemini-2.5-pro"
            assert orch_config.api_key == "google-key"

            cypher_config = config.active_cypher_config
            assert cypher_config.provider == "ollama"
            assert cypher_config.model_id == "codellama"
            assert cypher_config.endpoint == "http://localhost:11434/v1"

    def test_default_fallback_behavior(self) -> None:
        """Test that defaults work when no explicit provider is configured."""
        with patch.dict(os.environ, {}, clear=True):
            config = AppConfig(_env_file=None)  # ty: ignore[unknown-argument]

            orch_config = config.active_orchestrator_config
            assert orch_config.provider == "ollama"
            assert orch_config.model_id == "llama3.2"

            cypher_config = config.active_cypher_config
            assert cypher_config.provider == "ollama"
            assert cypher_config.model_id == "llama3.2"

    def test_bare_model_name_parsing(self) -> None:
        """Test that bare model names default to Ollama provider."""
        config = AppConfig()

        provider, model = config.parse_model_string("llama3.2")
        assert provider == "ollama"
        assert model == "llama3.2"

        provider, model = config.parse_model_string("mistral-7b")
        assert provider == "ollama"
        assert model == "mistral-7b"

    def test_batch_size_validation(self) -> None:
        """Test batch size validation and resolution."""
        config = AppConfig()

        assert config.resolve_batch_size(None) == 1000
        assert config.resolve_batch_size(5000) == 5000
        assert config.resolve_batch_size(1) == 1

        with pytest.raises(ValueError, match="batch_size must be a positive integer"):
            config.resolve_batch_size(0)

        with pytest.raises(ValueError, match="batch_size must be a positive integer"):
            config.resolve_batch_size(-1)

    def test_google_vertex_ai_configuration(self) -> None:
        """Test Google Vertex AI specific configuration."""
        with patch.dict(
            os.environ,
            {
                "ORCHESTRATOR_PROVIDER": "google",
                "ORCHESTRATOR_MODEL": "gemini-2.5-pro",
                "ORCHESTRATOR_PROJECT_ID": "test-project",
                "ORCHESTRATOR_REGION": "us-west1",
                "ORCHESTRATOR_PROVIDER_TYPE": "vertex",
                "ORCHESTRATOR_SERVICE_ACCOUNT_FILE": "/path/to/service-account.json",
            },
        ):
            config = AppConfig()

            orch_config = config.active_orchestrator_config
            assert orch_config.provider == "google"
            assert orch_config.model_id == "gemini-2.5-pro"
            assert orch_config.project_id == "test-project"
            assert orch_config.region == "us-west1"
            assert orch_config.provider_type == "vertex"
            assert orch_config.service_account_file == "/path/to/service-account.json"

    def test_thinking_budget_configuration(self) -> None:
        """Test thinking budget configuration for reasoning models."""
        with patch.dict(
            os.environ,
            {
                "ORCHESTRATOR_PROVIDER": "google",
                "ORCHESTRATOR_MODEL": "gemini-2.0-flash-thinking-exp",
                "ORCHESTRATOR_API_KEY": "test-key",
                "ORCHESTRATOR_THINKING_BUDGET": "10000",
            },
        ):
            config = AppConfig()

            orch_config = config.active_orchestrator_config
            assert orch_config.provider == "google"
            assert orch_config.model_id == "gemini-2.0-flash-thinking-exp"
            assert orch_config.thinking_budget == 10000

    def test_defaults_use_repo_root_env_file_instead_of_cwd_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """AppConfig should not read an unrelated .env from the current working directory."""
        foreign_env = tmp_path / ".env"
        foreign_env.write_text(
            "ABEY_POSTGRES_PORT=15432\nTARGET_REPO_PATH=./foreign-repo\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        with patch.dict(os.environ, {}, clear=True):
            config = AppConfig()

        assert AppConfig.model_config.get("env_file") == DEFAULT_ENV_FILE
        assert config.TARGET_REPO_PATH == "."

    def test_explicit_env_file_ignores_unrelated_keys(self, tmp_path) -> None:
        """Explicit dotenv files with extra keys should not fail settings construction."""
        env_file = tmp_path / "custom.env"
        env_file.write_text(
            "TARGET_REPO_PATH=./custom-repo\nABEY_POSTGRES_PORT=15432\nUNRELATED_FLAG=true\n",
            encoding="utf-8",
        )

        with patch.dict(os.environ, {}, clear=True):
            config = AppConfig(_env_file=env_file)  # ty: ignore[unknown-argument]

        assert config.TARGET_REPO_PATH == "./custom-repo"

    def test_openai_custom_endpoint(self) -> None:
        """Test OpenAI provider with custom endpoint."""
        with patch.dict(
            os.environ,
            {
                "ORCHESTRATOR_PROVIDER": "openai",
                "ORCHESTRATOR_MODEL": "gpt-4o",
                "ORCHESTRATOR_API_KEY": "sk-test-key",
                "ORCHESTRATOR_ENDPOINT": "https://api.custom-openai.com/v1",
            },
        ):
            config = AppConfig()

            orch_config = config.active_orchestrator_config
            assert orch_config.provider == "openai"
            assert orch_config.model_id == "gpt-4o"
            assert orch_config.api_key == "sk-test-key"
            assert orch_config.endpoint == "https://api.custom-openai.com/v1"

    def test_provider_specific_api_key_fallback_is_used_for_deepseek(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ORCHESTRATOR_PROVIDER": "deepseek",
                "ORCHESTRATOR_MODEL": "deepseek-chat",
                "DEEPSEEK_API_KEY": "deepseek-key",
            },
            clear=True,
        ):
            config = AppConfig(_env_file=None)  # ty: ignore[unknown-argument]

            orch_config = config.active_orchestrator_config
            assert orch_config.provider == "deepseek"
            assert orch_config.api_key == "deepseek-key"

    def test_provider_specific_api_key_beats_unrelated_generic_key(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "openai-key",
                "DEEPSEEK_API_KEY": "deepseek-key",
            },
            clear=True,
        ):
            assert resolve_provider_api_key("deepseek", None) == "deepseek-key"

    def test_explicit_role_api_key_beats_provider_fallback(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DEEPSEEK_API_KEY": "deepseek-key",
            },
            clear=True,
        ):
            assert resolve_provider_api_key("deepseek", "role-key") == "role-key"
