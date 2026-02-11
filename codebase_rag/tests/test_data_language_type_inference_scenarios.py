from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from codebase_rag.core import constants as cs
from codebase_rag.parsers.type_inference import TypeInferenceEngine


def _make_engine():
    return TypeInferenceEngine(
        import_processor=MagicMock(),
        function_registry=MagicMock(),
        simple_name_lookup=defaultdict(set),
        repo_path=Path("."),
        project_name="proj",
        ast_cache=MagicMock(),
        queries={},
        module_qn_to_file_path={},
        class_inheritance={},
    )


@pytest.mark.parametrize(
    "language",
    [
        cs.SupportedLanguage.YAML,
        cs.SupportedLanguage.JSON,
        cs.SupportedLanguage.HTML,
        cs.SupportedLanguage.CSS,
        cs.SupportedLanguage.SCSS,
        cs.SupportedLanguage.GRAPHQL,
        cs.SupportedLanguage.DOCKERFILE,
        cs.SupportedLanguage.SQL,
        cs.SupportedLanguage.VUE,
        cs.SupportedLanguage.SVELTE,
    ],
)
def test_data_language_type_inference_returns_empty_map(
    language: cs.SupportedLanguage,
) -> None:
    engine = _make_engine()
    result = engine.build_local_variable_type_map(MagicMock(), "proj.mod", language)
    assert result == {}
