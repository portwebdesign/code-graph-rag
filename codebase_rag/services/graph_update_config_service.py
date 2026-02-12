"""
This module defines the configuration settings for the graph update process.

It includes a `GraphUpdateConfig` data class to hold all the configuration
parameters and a `GraphUpdateConfigService` to load these settings from
environment variables. This provides a centralized way to manage the behavior
of the graph update pipeline, allowing for easy tuning and feature flagging.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class GraphUpdateConfig:
    """
    A data class to hold all configuration settings for the graph update process.

    This class is immutable (`frozen=True`) to ensure that the configuration
    is not changed during a run. Each attribute corresponds to a specific
    feature or setting that can be controlled via environment variables.
    """

    ast_cache_ttl: float | None
    selective_update_enabled: bool
    edge_only_update_enabled: bool
    incremental_cache_enabled: bool
    parse_cache_ttl: float | None
    git_delta_enabled: bool
    batch_parse_enabled: bool
    batch_parse_threaded: bool
    batch_workers: int
    pre_scan_enabled: bool
    perf_optimizer_enabled: bool
    perf_interval: int
    perf_memory: int | None
    profile_enabled: bool
    profile_interval: float
    profile_max: int
    declarative_enabled: bool
    cross_file_enabled: bool
    analysis_enabled: bool
    framework_metadata_enabled: bool
    tailwind_metadata_enabled: bool
    phase2_integration_enabled: bool
    phase2_embedding_strategy: str
    pass2_resolver_enabled: bool
    reparse_registry_enabled: bool
    parse_strict_enabled: bool


class GraphUpdateConfigService:
    """
    A service responsible for loading the graph update configuration from environment variables.
    """

    def load(self) -> GraphUpdateConfig:
        """
        Loads all configuration settings from environment variables and returns a `GraphUpdateConfig` object.

        This method reads various `CODEGRAPH_*` environment variables, provides
        sensible defaults, and populates the configuration data class.

        Returns:
            A `GraphUpdateConfig` instance with all the loaded settings.
        """
        ast_cache_ttl_env = os.getenv("CODEGRAPH_AST_CACHE_TTL")
        ast_cache_ttl = float(ast_cache_ttl_env) if ast_cache_ttl_env else None

        selective_update_enabled = os.getenv(
            "CODEGRAPH_SELECTIVE_UPDATE", ""
        ).lower() not in {"0", "false", "no"}
        edge_only_update_enabled = os.getenv(
            "CODEGRAPH_EDGE_ONLY_UPDATE", ""
        ).lower() not in {"0", "false", "no"}

        incremental_cache_enabled = os.getenv(
            "CODEGRAPH_INCREMENTAL_CACHE", ""
        ).lower() in {"1", "true", "yes"}
        parse_cache_ttl_env = os.getenv("CODEGRAPH_PARSE_CACHE_TTL")
        parse_cache_ttl = float(parse_cache_ttl_env) if parse_cache_ttl_env else None

        git_delta_enabled = os.getenv("CODEGRAPH_GIT_DELTA", "").lower() in {
            "1",
            "true",
            "yes",
        }

        batch_parse_enabled = os.getenv("CODEGRAPH_PARSE_BATCH", "").lower() in {
            "1",
            "true",
            "yes",
        }
        batch_parse_threaded = os.getenv("CODEGRAPH_PARSE_THREADPOOL", "").lower() in {
            "1",
            "true",
            "yes",
        }
        batch_workers_env = os.getenv("CODEGRAPH_PARSE_WORKERS")
        batch_workers = int(batch_workers_env) if batch_workers_env else 4

        pre_scan_enabled = os.getenv("CODEGRAPH_PRE_SCAN", "").lower() not in {
            "0",
            "false",
            "no",
        }

        perf_optimizer_enabled = os.getenv(
            "CODEGRAPH_PERF_OPTIMIZER", ""
        ).lower() not in {"0", "false", "no"}
        perf_interval_env = os.getenv("CODEGRAPH_PERF_OPTIMIZER_INTERVAL")
        perf_interval = int(perf_interval_env) if perf_interval_env else 200
        perf_memory_env = os.getenv("CODEGRAPH_PERF_OPTIMIZER_MEMORY_MB")
        perf_memory = int(perf_memory_env) if perf_memory_env else None

        profile_enabled = os.getenv("CODEGRAPH_MEMORY_PROFILE", "").lower() in {
            "1",
            "true",
            "yes",
        }
        profile_interval_env = os.getenv("CODEGRAPH_MEMORY_PROFILE_INTERVAL")
        profile_interval = float(profile_interval_env) if profile_interval_env else 5.0
        profile_max_env = os.getenv("CODEGRAPH_MEMORY_PROFILE_MAX")
        profile_max = int(profile_max_env) if profile_max_env else 500

        declarative_enabled = os.getenv("CODEGRAPH_DECLARATIVE_PARSER", "").lower() in {
            "1",
            "true",
            "yes",
        }
        cross_file_enabled = os.getenv(
            "CODEGRAPH_CROSS_FILE_RESOLVER", ""
        ).lower() not in {"0", "false", "no"}
        analysis_enabled = os.getenv("CODEGRAPH_ANALYSIS", "").lower() not in {
            "0",
            "false",
            "no",
        }
        framework_metadata_enabled = os.getenv(
            "CODEGRAPH_FRAMEWORK_METADATA", ""
        ).lower() in {"1", "true", "yes"}
        tailwind_metadata_enabled = os.getenv(
            "CODEGRAPH_TAILWIND_METADATA", ""
        ).lower() in {"1", "true", "yes"}
        phase2_integration_enabled = os.getenv(
            "CODEGRAPH_PHASE2_INTEGRATION", ""
        ).lower() not in {"0", "false", "no"}
        phase2_embedding_strategy = os.getenv(
            "CODEGRAPH_PHASE2_EMBEDDING_STRATEGY", "semantic"
        ).lower()
        pass2_resolver_enabled = os.getenv("CODEGRAPH_PASS2_RESOLVER", "").lower() in {
            "1",
            "true",
            "yes",
        }
        reparse_registry_enabled = os.getenv(
            "CODEGRAPH_REPARSE_REGISTRY", ""
        ).lower() not in {"0", "false", "no"}
        parse_strict_enabled = os.getenv("CODEGRAPH_PARSE_STRICT", "").lower() in {
            "1",
            "true",
            "yes",
        }

        return GraphUpdateConfig(
            ast_cache_ttl=ast_cache_ttl,
            selective_update_enabled=selective_update_enabled,
            edge_only_update_enabled=edge_only_update_enabled,
            incremental_cache_enabled=incremental_cache_enabled,
            parse_cache_ttl=parse_cache_ttl,
            git_delta_enabled=git_delta_enabled,
            batch_parse_enabled=batch_parse_enabled,
            batch_parse_threaded=batch_parse_threaded,
            batch_workers=batch_workers,
            pre_scan_enabled=pre_scan_enabled,
            perf_optimizer_enabled=perf_optimizer_enabled,
            perf_interval=perf_interval,
            perf_memory=perf_memory,
            profile_enabled=profile_enabled,
            profile_interval=profile_interval,
            profile_max=profile_max,
            declarative_enabled=declarative_enabled,
            cross_file_enabled=cross_file_enabled,
            analysis_enabled=analysis_enabled,
            framework_metadata_enabled=framework_metadata_enabled,
            tailwind_metadata_enabled=tailwind_metadata_enabled,
            phase2_integration_enabled=phase2_integration_enabled,
            phase2_embedding_strategy=phase2_embedding_strategy,
            pass2_resolver_enabled=pass2_resolver_enabled,
            reparse_registry_enabled=reparse_registry_enabled,
            parse_strict_enabled=parse_strict_enabled,
        )
