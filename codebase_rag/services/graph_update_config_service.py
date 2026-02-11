from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class GraphUpdateConfig:
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
    pass2_resolver_enabled: bool
    reparse_registry_enabled: bool
    parse_strict_enabled: bool


class GraphUpdateConfigService:
    def load(self) -> GraphUpdateConfig:
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
            pass2_resolver_enabled=pass2_resolver_enabled,
            reparse_registry_enabled=reparse_registry_enabled,
            parse_strict_enabled=parse_strict_enabled,
        )
