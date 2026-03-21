from __future__ import annotations

from collections import defaultdict
from collections.abc import ItemsView, KeysView
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

from codebase_rag.core import constants as cs
from codebase_rag.data_models.types_defs import (
    FunctionRegistryTrieProtocol,
    NodeType,
    QualifiedName,
)
from codebase_rag.parsers.pipeline.reparse_registry_resolver import (
    ReparseRegistryResolver,
)


class MockFunctionRegistry:
    def __init__(self) -> None:
        self._data: dict[QualifiedName, NodeType] = {}
        self._suffix_index: dict[str, list[QualifiedName]] = defaultdict(list)

    def __contains__(self, qn: QualifiedName) -> bool:
        return qn in self._data

    def __getitem__(self, qn: QualifiedName) -> NodeType:
        return self._data[qn]

    def __setitem__(self, qn: QualifiedName, func_type: NodeType) -> None:
        self._data[qn] = func_type
        parts = qn.split(cs.SEPARATOR_DOT)
        for i in range(len(parts)):
            suffix = cs.SEPARATOR_DOT.join(parts[i:])
            if qn not in self._suffix_index[suffix]:
                self._suffix_index[suffix].append(qn)

    def get(
        self, qn: QualifiedName, default: NodeType | None = None
    ) -> NodeType | None:
        return self._data.get(qn, default)

    def keys(self) -> KeysView[QualifiedName]:
        return self._data.keys()

    def items(self) -> ItemsView[QualifiedName, NodeType]:
        return self._data.items()

    def find_with_prefix(self, prefix: str) -> list[tuple[QualifiedName, NodeType]]:
        return [(k, v) for k, v in self._data.items() if k.startswith(prefix)]

    def find_ending_with(self, suffix: str) -> list[QualifiedName]:
        return self._suffix_index.get(suffix, [])


def _build_resolver(
    registry: MockFunctionRegistry,
    module_qn_to_file_path: dict[str, Path],
) -> ReparseRegistryResolver:
    return ReparseRegistryResolver(
        ingestor=MagicMock(),
        repo_path=Path("."),
        project_name="proj",
        queries={},
        function_registry=cast(FunctionRegistryTrieProtocol, registry),
        module_qn_to_file_path=module_qn_to_file_path,
    )


def test_reparse_prefers_prod_candidate_for_prod_caller() -> None:
    registry = MockFunctionRegistry()
    registry["proj.tests.helpers.helper"] = NodeType.FUNCTION
    registry["proj.app.helpers.helper"] = NodeType.FUNCTION

    resolver = _build_resolver(
        registry,
        {
            "proj.tests.helpers": Path("tests/helpers.py"),
            "proj.app.helpers": Path("src/helpers.py"),
        },
    )
    built = resolver._build_registry()

    qn, qtype = resolver._resolve_from_registry(
        "helper",
        built,
        "src/api/routes.py",
    )

    assert qn == "proj.app.helpers.helper"
    assert qtype == NodeType.FUNCTION.value


def test_reparse_drops_non_callable_candidates() -> None:
    registry = MockFunctionRegistry()
    registry["proj.db.users"] = NodeType.CLASS

    resolver = _build_resolver(
        registry,
        {"proj.db": Path("migrations/001_init.sql")},
    )
    built = resolver._build_registry()

    qn, qtype = resolver._resolve_from_registry(
        "users",
        built,
        "src/api/routes.py",
    )

    assert qn is None
    assert qtype is None


def test_reparse_uses_same_file_candidate_when_available() -> None:
    registry = MockFunctionRegistry()
    registry["proj.app.service.helper"] = NodeType.FUNCTION
    registry["proj.app.other.helper"] = NodeType.FUNCTION

    resolver = _build_resolver(
        registry,
        {
            "proj.app.service": Path("src/service.py"),
            "proj.app.other": Path("src/other.py"),
        },
    )
    built = resolver._build_registry()

    qn, qtype = resolver._resolve_from_registry(
        "helper",
        built,
        "src/service.py",
    )

    assert qn == "proj.app.service.helper"
    assert qtype == NodeType.FUNCTION.value
