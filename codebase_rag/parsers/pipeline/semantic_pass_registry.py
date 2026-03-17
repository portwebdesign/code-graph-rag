from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from tree_sitter import Node

from codebase_rag.core import constants as cs

DISABLED_FLAG_VALUES = {"0", "false", "no", "off"}


AstCacheItem = tuple[Path, tuple[Node, cs.SupportedLanguage]]


class SemanticPassProtocol(Protocol):
    def process_ast_cache(self, ast_cache_items: Iterable[AstCacheItem]) -> None: ...


@dataclass(frozen=True)
class SemanticPassContext:
    ingestor: object
    repo_path: Path
    project_name: str
    function_registry: object


@dataclass(frozen=True)
class SemanticPassDefinition:
    pass_id: str
    display_name: str
    env_flag: str
    order: int
    factory: Callable[[SemanticPassContext], SemanticPassProtocol]


def is_semantic_pass_enabled(env_flag: str, *, default: str = "1") -> bool:
    raw_value = os.getenv(env_flag, default).strip().lower()
    return raw_value not in DISABLED_FLAG_VALUES


def _build_contract_pass(ctx: SemanticPassContext) -> SemanticPassProtocol:
    from codebase_rag.parsers.pipeline.contract_semantics_pass import (
        ContractSemanticsPass,
    )

    return cast(
        SemanticPassProtocol,
        ContractSemanticsPass(
            ingestor=ctx.ingestor,
            repo_path=ctx.repo_path,
            project_name=ctx.project_name,
            function_registry=ctx.function_registry,
        ),
    )


def _build_event_flow_pass(ctx: SemanticPassContext) -> SemanticPassProtocol:
    from codebase_rag.parsers.pipeline.event_flow_pass import EventFlowPass

    return cast(
        SemanticPassProtocol,
        EventFlowPass(
            ingestor=ctx.ingestor,
            repo_path=ctx.repo_path,
            project_name=ctx.project_name,
            function_registry=ctx.function_registry,
        ),
    )


def _build_query_fingerprint_pass(ctx: SemanticPassContext) -> SemanticPassProtocol:
    from codebase_rag.parsers.pipeline.query_fingerprint_pass import (
        QueryFingerprintPass,
    )

    return cast(
        SemanticPassProtocol,
        QueryFingerprintPass(
            ingestor=ctx.ingestor,
            repo_path=ctx.repo_path,
            project_name=ctx.project_name,
            function_registry=ctx.function_registry,
        ),
    )


def _build_transaction_flow_pass(ctx: SemanticPassContext) -> SemanticPassProtocol:
    from codebase_rag.parsers.pipeline.transaction_flow_pass import TransactionFlowPass

    return cast(
        SemanticPassProtocol,
        TransactionFlowPass(
            ingestor=ctx.ingestor,
            repo_path=ctx.repo_path,
            project_name=ctx.project_name,
            function_registry=ctx.function_registry,
        ),
    )


def _build_frontend_operation_pass(ctx: SemanticPassContext) -> SemanticPassProtocol:
    from codebase_rag.parsers.pipeline.frontend_operation_pass import (
        FrontendOperationPass,
    )

    return cast(
        SemanticPassProtocol,
        FrontendOperationPass(
            ingestor=ctx.ingestor,
            repo_path=ctx.repo_path,
            project_name=ctx.project_name,
            function_registry=ctx.function_registry,
        ),
    )


def _build_config_semantics_pass(ctx: SemanticPassContext) -> SemanticPassProtocol:
    from codebase_rag.parsers.pipeline.config_semantics_pass import (
        ConfigSemanticsPass,
    )

    return cast(
        SemanticPassProtocol,
        ConfigSemanticsPass(
            ingestor=ctx.ingestor,
            repo_path=ctx.repo_path,
            project_name=ctx.project_name,
            function_registry=ctx.function_registry,
        ),
    )


def _build_test_semantics_pass(ctx: SemanticPassContext) -> SemanticPassProtocol:
    from codebase_rag.parsers.pipeline.test_semantics_pass import TestSemanticsPass

    return cast(
        SemanticPassProtocol,
        TestSemanticsPass(
            ingestor=ctx.ingestor,
            repo_path=ctx.repo_path,
            project_name=ctx.project_name,
            function_registry=ctx.function_registry,
        ),
    )


def default_semantic_pass_definitions() -> tuple[SemanticPassDefinition, ...]:
    return (
        SemanticPassDefinition(
            pass_id="contract_semantics",
            display_name="contract semantics",
            env_flag="CODEGRAPH_CONTRACT_SEMANTICS",
            order=100,
            factory=_build_contract_pass,
        ),
        SemanticPassDefinition(
            pass_id="event_flow_semantics",
            display_name="event flow semantics",
            env_flag="CODEGRAPH_EVENT_FLOW_SEMANTICS",
            order=200,
            factory=_build_event_flow_pass,
        ),
        SemanticPassDefinition(
            pass_id="query_fingerprint_semantics",
            display_name="query fingerprint semantics",
            env_flag="CODEGRAPH_QUERY_FINGERPRINT_SEMANTICS",
            order=250,
            factory=_build_query_fingerprint_pass,
        ),
        SemanticPassDefinition(
            pass_id="transaction_flow_semantics",
            display_name="transaction flow semantics",
            env_flag="CODEGRAPH_TRANSACTION_FLOW_SEMANTICS",
            order=300,
            factory=_build_transaction_flow_pass,
        ),
        SemanticPassDefinition(
            pass_id="frontend_operation_semantics",
            display_name="frontend operation semantics",
            env_flag="CODEGRAPH_FRONTEND_OPERATION_SEMANTICS",
            order=350,
            factory=_build_frontend_operation_pass,
        ),
        SemanticPassDefinition(
            pass_id="config_semantics",
            display_name="config semantics",
            env_flag="CODEGRAPH_CONFIG_SEMANTICS",
            order=375,
            factory=_build_config_semantics_pass,
        ),
        SemanticPassDefinition(
            pass_id="test_semantics",
            display_name="test semantics",
            env_flag="CODEGRAPH_TEST_SEMANTICS",
            order=400,
            factory=_build_test_semantics_pass,
        ),
    )


class SemanticPassRegistry:
    def __init__(
        self,
        context: SemanticPassContext,
        definitions: Sequence[SemanticPassDefinition] | None = None,
    ) -> None:
        self.context = context
        self._definitions = tuple(
            sorted(
                definitions or default_semantic_pass_definitions(),
                key=lambda definition: (definition.order, definition.pass_id),
            )
        )

    def ordered_definitions(self) -> tuple[SemanticPassDefinition, ...]:
        return self._definitions

    def enabled_definitions(self) -> tuple[SemanticPassDefinition, ...]:
        return tuple(
            definition
            for definition in self._definitions
            if is_semantic_pass_enabled(definition.env_flag)
        )

    def run_enabled(self, ast_cache_items: Iterable[AstCacheItem]) -> list[str]:
        items = tuple(ast_cache_items)
        executed: list[str] = []
        for definition in self.enabled_definitions():
            definition.factory(self.context).process_ast_cache(items)
            executed.append(definition.pass_id)
        return executed
