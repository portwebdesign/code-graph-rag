from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from codebase_rag.parsers.frameworks.framework_linker import (
    EndpointMatch,
    FrameworkLinker,
)


def test_ensure_endpoint_node_writes_canonical_and_alias_fields(tmp_path: Path) -> None:
    ingestor = MagicMock()
    linker = FrameworkLinker(
        repo_path=tmp_path,
        project_name="demo",
        ingestor=ingestor,
        function_registry=MagicMock(),
        simple_name_lookup={},
    )

    endpoint = EndpointMatch(framework="fastapi", method="GET", path="/v1/users")
    linker._ensure_endpoint_node(endpoint, "app/routes.py")

    call_args = ingestor.ensure_node_batch.call_args
    assert call_args is not None
    props = call_args.args[1]
    assert props["route_path"] == "/v1/users"
    assert props["http_method"] == "GET"
    assert props["route"] == "/v1/users"
    assert props["method"] == "GET"
