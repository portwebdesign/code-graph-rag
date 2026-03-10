from __future__ import annotations

from types import SimpleNamespace

import pytest

from codebase_rag.core import constants as cs
from codebase_rag.infrastructure import parser_loader


def test_windows_guard_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parser_loader.os, "name", "nt", raising=False)
    monkeypatch.delenv("CODEGRAPH_WINDOWS_BLOCK_LANGS", raising=False)

    assert parser_loader._is_windows_unsupported(cs.SupportedLanguage.KOTLIN) is False
    assert parser_loader._is_windows_unsupported(cs.SupportedLanguage.VUE) is False
    assert parser_loader._is_windows_unsupported(cs.SupportedLanguage.PYTHON) is False


def test_windows_guard_supports_explicit_blocklist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(parser_loader.os, "name", "nt", raising=False)
    monkeypatch.setenv("CODEGRAPH_WINDOWS_BLOCK_LANGS", "kotlin,vue")

    assert parser_loader._is_windows_unsupported(cs.SupportedLanguage.KOTLIN) is True
    assert parser_loader._is_windows_unsupported(cs.SupportedLanguage.VUE) is True


def test_import_language_loaders_falls_back_to_html_for_vue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_try_import_language(
        module_path: str,
        attr_name: str,
        lang_name: cs.SupportedLanguage,
    ) -> object | None:
        _ = attr_name
        if lang_name == cs.SupportedLanguage.HTML:
            return lambda: "html-loader"
        if module_path.endswith("vue"):
            return None
        return lambda: f"{lang_name}-loader"

    monkeypatch.setattr(
        parser_loader,
        "_try_import_language",
        fake_try_import_language,
    )
    monkeypatch.setattr(
        parser_loader,
        "_try_load_from_submodule",
        lambda lang_name: None
        if lang_name == cs.SupportedLanguage.VUE
        else (lambda: None),
    )

    loaders = parser_loader._import_language_loaders()

    assert loaders[cs.SupportedLanguage.VUE] is loaders[cs.SupportedLanguage.HTML]


def test_coerce_language_wraps_int_pointer_without_deprecated_constructor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_capsule(ptr: int) -> object:
        captured["ptr"] = ptr
        return "capsule"

    class FakeLanguage:
        def __new__(cls, obj: object) -> object:
            captured["arg"] = obj
            return SimpleNamespace(language=obj)

    monkeypatch.setattr(parser_loader, "_capsule_from_language_ptr", fake_capsule)
    monkeypatch.setattr(parser_loader, "Language", FakeLanguage)

    language = parser_loader._coerce_language(1234)

    assert captured["ptr"] == 1234
    assert captured["arg"] == "capsule"
    assert getattr(language, "language") == "capsule"
