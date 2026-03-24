from __future__ import annotations

import tomllib
from importlib import import_module
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_cgr_shim_exports_cli_app() -> None:
    module = import_module("cgr")
    cli_module = import_module("codebase_rag.cli")

    assert hasattr(module, "app")
    assert module.app is cli_module.app


def test_setuptools_package_discovery_includes_all_public_namespaces() -> None:
    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )

    find_config = pyproject["tool"]["setuptools"]["packages"]["find"]

    assert "codebase_rag*" in find_config["include"]
    assert "codec*" in find_config["include"]
    assert "cgr*" in find_config["include"]
    assert "codebase_rag.tests*" in find_config["exclude"]
