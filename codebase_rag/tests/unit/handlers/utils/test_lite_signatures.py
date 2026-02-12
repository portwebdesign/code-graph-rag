from __future__ import annotations

from codebase_rag.core import constants as cs
from codebase_rag.parsers.core.utils import build_lite_signature


def test_build_lite_signature_python() -> None:
    signature = build_lite_signature(
        "process_data",
        ["input_file", "validate"],
        "Dict[str, Any]",
        cs.SupportedLanguage.PYTHON,
    )
    assert signature == "process_data(input_file, validate) -> Dict[str, Any]"


def test_build_lite_signature_js() -> None:
    signature = build_lite_signature(
        "fetchData",
        ["url"],
        "Promise<Response>",
        cs.SupportedLanguage.JS,
    )
    assert signature == "fetchData(url): Promise<Response>"


def test_build_lite_signature_ruby() -> None:
    signature = build_lite_signature(
        "render",
        ["template"],
        "String",
        cs.SupportedLanguage.RUBY,
    )
    assert signature == "render(template) # => String"


def test_build_lite_signature_empty_params() -> None:
    signature = build_lite_signature(
        "main",
        [],
        None,
        cs.SupportedLanguage.PYTHON,
    )
    assert signature == "main()"
