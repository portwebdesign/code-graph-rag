from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from loguru import logger
from tree_sitter import Node

from codebase_rag.core import constants as cs
from codebase_rag.core.config_semantic_identity import (
    build_env_var_qn,
    build_feature_flag_qn,
    build_secret_ref_qn,
    is_feature_flag_name,
    is_secret_like_name,
    parse_env_truthiness,
)
from codebase_rag.parsers.config.config_parser import ConfigParserMixin
from codebase_rag.parsers.pipeline.config_semantics import (
    CodeEnvObservation,
    ConfigDefinition,
    extract_dotenv_definitions,
    extract_kubernetes_env_bindings,
    extract_python_env_observations,
    extract_typescript_env_observations,
)
from codebase_rag.parsers.pipeline.semantic_guardrails import (
    SEMANTIC_GUARDRAIL_LIMITS,
    apply_grouped_guardrail,
    apply_sequence_guardrail,
)
from codebase_rag.parsers.pipeline.semantic_metadata import build_semantic_metadata
from codebase_rag.parsers.pipeline.semantic_pass_registry import (
    is_semantic_pass_enabled,
)


class ConfigSemanticsPass(ConfigParserMixin):
    """Emits first-wave config/env/flag/secret semantic graph edges."""

    _SKIP_DIRS = {
        ".git",
        ".idea",
        ".next",
        ".venv",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "venv",
    }

    def __init__(
        self,
        ingestor,
        repo_path: Path,
        project_name: str,
        function_registry,
    ) -> None:
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.project_name = project_name
        self.function_registry = function_registry
        self.enabled = is_semantic_pass_enabled("CODEGRAPH_CONFIG_SEMANTICS")

    def process_ast_cache(
        self,
        ast_items: Iterable[tuple[Path, tuple[Node, cs.SupportedLanguage]]],
    ) -> None:
        if not self.enabled:
            return

        ast_cache_items = tuple(ast_items)
        definition_count = 0
        edge_count = 0

        for file_path in self._iter_repo_files(limit=400):
            relative_path = self._relative_path(file_path)
            source = self._read_source(file_path)
            if source is None:
                continue

            if file_path.name.startswith(".env"):
                definitions = apply_sequence_guardrail(
                    extract_dotenv_definitions(source),
                    limit=SEMANTIC_GUARDRAIL_LIMITS["config_definitions_per_file"],
                    pass_id="config_semantics",
                    budget_name="config_definitions_per_file",
                    scope=relative_path,
                )
                for definition in definitions:
                    definition_count += self._emit_definition(
                        definition=definition,
                        relative_path=relative_path,
                    )
                continue

            config_type = self.detect_config_type(relative_path)
            if config_type == "docker-compose":
                definitions = apply_sequence_guardrail(
                    self._extract_docker_compose_definitions(source),
                    limit=SEMANTIC_GUARDRAIL_LIMITS["config_definitions_per_file"],
                    pass_id="config_semantics",
                    budget_name="config_definitions_per_file",
                    scope=relative_path,
                )
                for definition in definitions:
                    definition_count += self._emit_definition(
                        definition=definition,
                        relative_path=relative_path,
                    )
            elif config_type == "kubernetes":
                definitions = apply_sequence_guardrail(
                    self._extract_kubernetes_definitions(source),
                    limit=SEMANTIC_GUARDRAIL_LIMITS["config_definitions_per_file"],
                    pass_id="config_semantics",
                    budget_name="config_definitions_per_file",
                    scope=relative_path,
                )
                for definition in definitions:
                    definition_count += self._emit_definition(
                        definition=definition,
                        relative_path=relative_path,
                    )

        for file_path, (_, language) in ast_cache_items:
            source = self._read_source(file_path)
            if source is None:
                continue
            relative_path = self._relative_path(file_path)
            module_qn = self._module_qn_for_path(file_path)

            observations: list[CodeEnvObservation] = []
            if (
                language == cs.SupportedLanguage.PYTHON
                and file_path.suffix == cs.EXT_PY
            ):
                observations = extract_python_env_observations(source)
            elif language in {
                cs.SupportedLanguage.JS,
                cs.SupportedLanguage.TS,
            } and file_path.suffix in {*cs.JS_EXTENSIONS, *cs.TS_EXTENSIONS}:
                observations = extract_typescript_env_observations(
                    source,
                    relative_path=relative_path,
                )

            observations = apply_grouped_guardrail(
                observations,
                group_key=lambda observation: observation.source_name,
                limit_per_group=SEMANTIC_GUARDRAIL_LIMITS[
                    "config_observations_per_source"
                ],
                pass_id="config_semantics",
                budget_name="config_observations_per_source",
                scope=relative_path,
            )
            observations = apply_sequence_guardrail(
                observations,
                limit=SEMANTIC_GUARDRAIL_LIMITS["config_observations_per_file"],
                pass_id="config_semantics",
                budget_name="config_observations_per_file",
                scope=relative_path,
            )
            for observation in observations:
                edge_count += self._emit_code_observation(
                    observation=observation,
                    relative_path=relative_path,
                    module_qn=module_qn,
                )

        logger.info(
            "ConfigSemanticsPass: {} definition node(s), {} edge(s)",
            definition_count,
            edge_count,
        )

    def _emit_definition(
        self,
        *,
        definition: ConfigDefinition,
        relative_path: str,
    ) -> int:
        emitted = 0
        self._ensure_env_var_node(
            env_name=definition.env_name,
            relative_path=relative_path,
            source_kind=definition.source_kind,
            has_definition=True,
        )
        emitted += 1

        if is_feature_flag_name(definition.env_name):
            self._ensure_feature_flag_node(
                env_name=definition.env_name,
                relative_path=relative_path,
                source_kind=definition.source_kind,
                has_definition=True,
                default_enabled=definition.default_enabled,
            )
            emitted += 1

        if is_secret_like_name(definition.env_name) or definition.secret_provider:
            self._ensure_secret_ref_node(
                secret_name=definition.env_name,
                relative_path=relative_path,
                source_kind=definition.source_kind,
                has_definition=True,
                secret_provider=definition.secret_provider,
                secret_key=definition.secret_key,
            )
            emitted += 1

        return emitted

    def _emit_code_observation(
        self,
        *,
        observation: CodeEnvObservation,
        relative_path: str,
        module_qn: str,
    ) -> int:
        source_spec = self._resolve_source_spec(
            module_qn=module_qn,
            source_name=observation.source_name,
            source_kind=observation.source_kind,
        )
        edge_count = 0

        env_qn = self._ensure_env_var_node(
            env_name=observation.env_name,
            relative_path=relative_path,
            source_kind="code_reader",
            has_reader=True,
        )
        if observation.reads_env:
            self.ingestor.ensure_relationship_batch(
                source_spec,
                cs.RelationshipType.READS_ENV,
                (cs.NodeLabel.ENV_VAR, cs.KEY_QUALIFIED_NAME, env_qn),
                self._metadata(
                    relative_path=relative_path,
                    evidence_kind=observation.evidence_kind,
                    line_start=observation.line_start,
                    line_end=observation.line_end,
                    extra={"env_name": observation.env_name},
                ),
            )
            edge_count += 1

        if observation.uses_secret or is_secret_like_name(observation.env_name):
            secret_qn = self._ensure_secret_ref_node(
                secret_name=observation.env_name,
                relative_path=relative_path,
                source_kind="code_reader",
                has_reader=True,
            )
            self.ingestor.ensure_relationship_batch(
                source_spec,
                cs.RelationshipType.USES_SECRET,
                (cs.NodeLabel.SECRET_REF, cs.KEY_QUALIFIED_NAME, secret_qn),
                self._metadata(
                    relative_path=relative_path,
                    evidence_kind="secret_reader",
                    line_start=observation.line_start,
                    line_end=observation.line_end,
                    extra={"secret_name": observation.env_name},
                ),
            )
            edge_count += 1

        if observation.gates_flag or is_feature_flag_name(observation.env_name):
            feature_qn = self._ensure_feature_flag_node(
                env_name=observation.env_name,
                relative_path=relative_path,
                source_kind="code_reader",
                has_reader=True,
            )
            if observation.gates_flag:
                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.FEATURE_FLAG, cs.KEY_QUALIFIED_NAME, feature_qn),
                    cs.RelationshipType.GATES_CODE_PATH,
                    source_spec,
                    self._metadata(
                        relative_path=relative_path,
                        evidence_kind="feature_flag_gate",
                        line_start=observation.line_start,
                        line_end=observation.line_end,
                        extra={"flag_name": observation.env_name},
                    ),
                )
                edge_count += 1

        return edge_count

    def _extract_docker_compose_definitions(
        self, source: str
    ) -> list[ConfigDefinition]:
        definitions: list[ConfigDefinition] = []
        for service in self.extract_docker_compose(source):
            for env_name, raw_value in service.environment.items():
                definitions.append(
                    ConfigDefinition(
                        env_name=env_name,
                        source_kind="docker_compose",
                        source_name=service.name,
                        default_enabled=(
                            None
                            if not is_feature_flag_name(env_name)
                            else self._feature_default(raw_value)
                        ),
                    )
                )
        return definitions

    def _extract_kubernetes_definitions(self, source: str) -> list[ConfigDefinition]:
        definitions: list[ConfigDefinition] = []
        for binding in extract_kubernetes_env_bindings(source):
            definitions.append(
                ConfigDefinition(
                    env_name=binding.env_name,
                    source_kind="kubernetes",
                    source_name=f"{binding.resource_kind}:{binding.resource_name}",
                    default_enabled=(
                        None
                        if not is_feature_flag_name(binding.env_name)
                        else self._feature_default(binding.literal_value)
                    ),
                    secret_provider=binding.secret_name,
                    secret_key=binding.secret_key,
                )
            )
        return definitions

    def _ensure_env_var_node(
        self,
        *,
        env_name: str,
        relative_path: str,
        source_kind: str,
        has_definition: bool = False,
        has_reader: bool = False,
    ) -> str:
        env_qn = build_env_var_qn(self.project_name, env_name)
        props = {
            cs.KEY_QUALIFIED_NAME: env_qn,
            cs.KEY_NAME: env_name,
            "source_kind": source_kind,
        }
        if has_definition:
            props["has_definition"] = True
        if has_reader:
            props["has_reader"] = True
        props.update(
            self._metadata(
                relative_path=relative_path,
                evidence_kind="env_var",
                extra={
                    "is_secret_like": is_secret_like_name(env_name),
                    "is_feature_flag": is_feature_flag_name(env_name),
                },
            )
        )
        self.ingestor.ensure_node_batch(cs.NodeLabel.ENV_VAR, props)
        return env_qn

    def _ensure_feature_flag_node(
        self,
        *,
        env_name: str,
        relative_path: str,
        source_kind: str,
        has_definition: bool = False,
        has_reader: bool = False,
        default_enabled: bool | None = None,
    ) -> str:
        feature_qn = build_feature_flag_qn(self.project_name, env_name)
        props = {
            cs.KEY_QUALIFIED_NAME: feature_qn,
            cs.KEY_NAME: env_name,
            "source_kind": source_kind,
        }
        if has_definition:
            props["has_definition"] = True
        if has_reader:
            props["has_reader"] = True
        if default_enabled is not None:
            props["default_enabled"] = default_enabled
        props.update(
            self._metadata(
                relative_path=relative_path,
                evidence_kind="feature_flag",
            )
        )
        self.ingestor.ensure_node_batch(cs.NodeLabel.FEATURE_FLAG, props)
        return feature_qn

    def _ensure_secret_ref_node(
        self,
        *,
        secret_name: str,
        relative_path: str,
        source_kind: str,
        has_definition: bool = False,
        has_reader: bool = False,
        secret_provider: str | None = None,
        secret_key: str | None = None,
    ) -> str:
        secret_qn = build_secret_ref_qn(self.project_name, secret_name)
        props = {
            cs.KEY_QUALIFIED_NAME: secret_qn,
            cs.KEY_NAME: secret_name,
            "source_kind": source_kind,
            "masked": True,
        }
        if has_definition:
            props["has_definition"] = True
        if has_reader:
            props["has_reader"] = True
        if secret_provider:
            props["secret_provider"] = secret_provider
        if secret_key:
            props["secret_key"] = secret_key
        props.update(
            self._metadata(
                relative_path=relative_path,
                evidence_kind="secret_ref",
            )
        )
        self.ingestor.ensure_node_batch(cs.NodeLabel.SECRET_REF, props)
        return secret_qn

    def _resolve_source_spec(
        self,
        *,
        module_qn: str,
        source_name: str,
        source_kind: str,
    ) -> tuple[str, str, str]:
        if source_kind == "module" or source_name == "__module__":
            return (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn)
        if source_kind == "class":
            return (
                cs.NodeLabel.CLASS,
                cs.KEY_QUALIFIED_NAME,
                f"{module_qn}{cs.SEPARATOR_DOT}{source_name}",
            )
        preferred_qn = f"{module_qn}{cs.SEPARATOR_DOT}{source_name}"
        node_type = self.function_registry.get(preferred_qn)
        if node_type is not None:
            return (node_type.value, cs.KEY_QUALIFIED_NAME, preferred_qn)
        for candidate in self.function_registry.find_ending_with(source_name):
            if candidate.startswith(f"{module_qn}{cs.SEPARATOR_DOT}"):
                candidate_type = self.function_registry.get(candidate)
                if candidate_type is not None:
                    return (candidate_type.value, cs.KEY_QUALIFIED_NAME, candidate)
        return (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn)

    def _iter_repo_files(self, *, limit: int) -> list[Path]:
        files: list[Path] = []
        for root, dirs, file_names in os.walk(self.repo_path):
            dirs[:] = [
                directory for directory in dirs if directory not in self._SKIP_DIRS
            ]
            for file_name in file_names:
                file_path = Path(root) / file_name
                files.append(file_path)
                if len(files) >= limit:
                    return files
        return files

    def _module_qn_for_path(self, file_path: Path) -> str:
        relative_path = file_path.relative_to(self.repo_path)
        parts = list(relative_path.with_suffix("").parts)
        if file_path.name == cs.INIT_PY:
            parts = list(relative_path.parent.parts)
        return cs.SEPARATOR_DOT.join([self.project_name, *parts])

    def _relative_path(self, file_path: Path) -> str:
        return str(file_path.relative_to(self.repo_path)).replace("\\", "/")

    def _metadata(
        self,
        *,
        relative_path: str,
        evidence_kind: str,
        line_start: int | None = None,
        line_end: int | None = None,
        extra: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return build_semantic_metadata(
            source_parser="config_semantics_pass",
            evidence_kind=evidence_kind,
            file_path=relative_path,
            confidence=0.86,
            line_start=line_start,
            line_end=line_end,
            extra=extra,
        )

    @staticmethod
    def _feature_default(value: object) -> bool | None:
        return parse_env_truthiness(value)

    @staticmethod
    def _read_source(file_path: Path) -> str | None:
        try:
            return file_path.read_text(encoding=cs.ENCODING_UTF8)
        except Exception:
            return None
