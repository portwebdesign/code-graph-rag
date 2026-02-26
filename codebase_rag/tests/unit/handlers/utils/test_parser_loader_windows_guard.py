from __future__ import annotations

import pytest

from codebase_rag.core import constants as cs
from codebase_rag.infrastructure import parser_loader


def test_windows_guard_blocks_graphql_and_vue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parser_loader.os, "name", "nt", raising=False)
    monkeypatch.delenv("CODEGRAPH_WINDOWS_ALLOW_UNSUPPORTED", raising=False)

    assert parser_loader._is_windows_unsupported(cs.SupportedLanguage.KOTLIN) is True
    assert parser_loader._is_windows_unsupported(cs.SupportedLanguage.VUE) is True
    assert parser_loader._is_windows_unsupported(cs.SupportedLanguage.PYTHON) is False


def test_windows_guard_allows_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parser_loader.os, "name", "nt", raising=False)
    monkeypatch.setenv("CODEGRAPH_WINDOWS_ALLOW_UNSUPPORTED", "1")

    assert parser_loader._is_windows_unsupported(cs.SupportedLanguage.KOTLIN) is False
    assert parser_loader._is_windows_unsupported(cs.SupportedLanguage.VUE) is False
