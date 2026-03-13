from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

from codebase_rag.core import constants as cs
from codebase_rag.tests.conftest import get_nodes, get_relationships, run_updater


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _endpoint_props(mock_ingestor: MagicMock) -> list[dict[str, object]]:
    endpoint_calls = get_nodes(mock_ingestor, cs.NodeLabel.ENDPOINT)
    return [cast(dict[str, object], call[0][1]) for call in endpoint_calls]


def _relationship_payloads(mock_ingestor: MagicMock, rel_type: str) -> list[tuple]:
    return [call.args for call in get_relationships(mock_ingestor, rel_type)]


def test_materializes_fastapi_wrapper_chain(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = temp_repo / "fastapi_comp"
    project.mkdir()

    _write(
        project / "src/api/app_factory.py",
        """from fastapi import FastAPI
from src.api.routes.v1 import router as v1_router


def create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(v1_router)
    return app
""",
    )
    _write(
        project / "src/api/routes/v1/__init__.py",
        """from fastapi import APIRouter
from .accounting import router as accounting_router

router = APIRouter()
router.include_router(accounting_router)
""",
    )
    _write(
        project / "src/api/routes/v1/accounting.py",
        """from fastapi import APIRouter
from src.api.routers.accounting import router as accounting_router

router = APIRouter()
router.include_router(accounting_router, prefix="/api/v1")
""",
    )
    _write(
        project / "src/api/routers/accounting.py",
        """from fastapi import APIRouter

router = APIRouter()


@router.get("/tax-declarations")
async def list_tax_declarations() -> dict[str, str]:
    return {"status": "ok"}
""",
    )

    run_updater(project, mock_ingestor)

    endpoint_nodes = _endpoint_props(mock_ingestor)
    assert any(
        props.get(cs.KEY_ROUTE_PATH) == "/api/v1/tax-declarations"
        and props.get(cs.KEY_FRAMEWORK) == "fastapi"
        for props in endpoint_nodes
    )

    mounts = _relationship_payloads(mock_ingestor, cs.RelationshipType.MOUNTS_ROUTER)
    assert any(
        rel[0]
        == (
            cs.NodeLabel.MODULE,
            cs.KEY_QUALIFIED_NAME,
            "fastapi_comp.src.api.app_factory",
        )
        and rel[2]
        == (
            cs.NodeLabel.MODULE,
            cs.KEY_QUALIFIED_NAME,
            "fastapi_comp.src.api.routes.v1",
        )
        for rel in mounts
    )

    includes = _relationship_payloads(
        mock_ingestor, cs.RelationshipType.INCLUDES_ROUTER
    )
    assert any(
        rel[0]
        == (
            cs.NodeLabel.MODULE,
            cs.KEY_QUALIFIED_NAME,
            "fastapi_comp.src.api.routes.v1",
        )
        and rel[2]
        == (
            cs.NodeLabel.MODULE,
            cs.KEY_QUALIFIED_NAME,
            "fastapi_comp.src.api.routes.v1.accounting",
        )
        for rel in includes
    )
    assert any(
        rel[0]
        == (
            cs.NodeLabel.MODULE,
            cs.KEY_QUALIFIED_NAME,
            "fastapi_comp.src.api.routes.v1.accounting",
        )
        and rel[2]
        == (
            cs.NodeLabel.MODULE,
            cs.KEY_QUALIFIED_NAME,
            "fastapi_comp.src.api.routers.accounting",
        )
        and cast(dict[str, object], rel[3]).get("prefix") == "/api/v1"
        for rel in includes
    )

    prefixes = _relationship_payloads(
        mock_ingestor, cs.RelationshipType.PREFIXES_ENDPOINT
    )
    assert any(
        rel[0]
        == (
            cs.NodeLabel.MODULE,
            cs.KEY_QUALIFIED_NAME,
            "fastapi_comp.src.api.routes.v1.accounting",
        )
        and cast(dict[str, object], rel[3]).get("route_path")
        == "/api/v1/tax-declarations"
        and cast(dict[str, object], rel[3]).get("prefix") == "/api/v1"
        for rel in prefixes
    )

    has_endpoint = _relationship_payloads(
        mock_ingestor, cs.RelationshipType.HAS_ENDPOINT
    )
    assert any(
        rel[2]
        == (
            cs.NodeLabel.ENDPOINT,
            cs.KEY_QUALIFIED_NAME,
            "fastapi_comp.endpoint.fastapi.GET:/api/v1/tax-declarations",
        )
        for rel in has_endpoint
    )


def test_materializes_local_router_includes_with_prefix(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = temp_repo / "fastapi_local_comp"
    project.mkdir()

    _write(
        project / "main.py",
        """from fastapi import APIRouter, FastAPI

api = APIRouter()
admin = APIRouter()
app = FastAPI()


@admin.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


api.include_router(admin, prefix="/internal")
app.include_router(api, prefix="/api")
""",
    )

    run_updater(project, mock_ingestor)

    endpoint_nodes = _endpoint_props(mock_ingestor)
    assert any(
        props.get(cs.KEY_ROUTE_PATH) == "/api/internal/health"
        for props in endpoint_nodes
    )

    mounts = _relationship_payloads(mock_ingestor, cs.RelationshipType.MOUNTS_ROUTER)
    assert any(
        rel[0]
        == (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, "fastapi_local_comp.main")
        and rel[2]
        == (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, "fastapi_local_comp.main")
        and cast(dict[str, object], rel[3]).get("target_name") == "app"
        and cast(dict[str, object], rel[3]).get("source_var") == "api"
        and cast(dict[str, object], rel[3]).get("prefix") == "/api"
        for rel in mounts
    )

    includes = _relationship_payloads(
        mock_ingestor, cs.RelationshipType.INCLUDES_ROUTER
    )
    assert any(
        rel[0]
        == (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, "fastapi_local_comp.main")
        and rel[2]
        == (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, "fastapi_local_comp.main")
        and cast(dict[str, object], rel[3]).get("target_name") == "api"
        and cast(dict[str, object], rel[3]).get("source_var") == "admin"
        and cast(dict[str, object], rel[3]).get("prefix") == "/internal"
        for rel in includes
    )

    prefixes = _relationship_payloads(
        mock_ingestor, cs.RelationshipType.PREFIXES_ENDPOINT
    )
    prefix_values = [cast(dict[str, object], rel[3]).get("prefix") for rel in prefixes]
    assert "/api" in prefix_values
    assert "/internal" in prefix_values


def test_materializes_loop_and_clone_router_composition(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = temp_repo / "fastapi_legacy_comp"
    project.mkdir()

    _write(
        project / "src/api/routes/_clone.py",
        """from fastapi import APIRouter


def clone_router(source_router: APIRouter, **_: object) -> APIRouter:
    return source_router
""",
    )
    _write(
        project / "src/api/routes/legacy/__init__.py",
        """from fastapi import APIRouter
from src.api.routers.accounting import router as accounting_router
from src.api.routers.identity import router as identity_router
from src.api.routes._clone import clone_router

router = APIRouter()
for source_router in (
    identity_router,
    accounting_router,
):
    router.include_router(clone_router(source_router, deprecated=True))
""",
    )
    _write(
        project / "src/api/routers/accounting.py",
        """from fastapi import APIRouter

router = APIRouter()


@router.get("/tax-declarations")
async def list_tax_declarations() -> dict[str, str]:
    return {"status": "ok"}
""",
    )
    _write(
        project / "src/api/routers/identity.py",
        """from fastapi import APIRouter

router = APIRouter()


@router.get("/users")
async def list_users() -> dict[str, str]:
    return {"status": "ok"}
""",
    )

    run_updater(project, mock_ingestor)

    includes = _relationship_payloads(
        mock_ingestor, cs.RelationshipType.INCLUDES_ROUTER
    )
    clone_edges = [
        rel
        for rel in includes
        if rel[0]
        == (
            cs.NodeLabel.MODULE,
            cs.KEY_QUALIFIED_NAME,
            "fastapi_legacy_comp.src.api.routes.legacy",
        )
    ]
    assert len(clone_edges) >= 2
    assert any(
        rel[2]
        == (
            cs.NodeLabel.MODULE,
            cs.KEY_QUALIFIED_NAME,
            "fastapi_legacy_comp.src.api.routers.identity",
        )
        and cast(dict[str, object], rel[3]).get("composition_kind") == "clone_router"
        for rel in clone_edges
    )
    assert any(
        rel[2]
        == (
            cs.NodeLabel.MODULE,
            cs.KEY_QUALIFIED_NAME,
            "fastapi_legacy_comp.src.api.routers.accounting",
        )
        and cast(dict[str, object], rel[3]).get("composition_kind") == "clone_router"
        for rel in clone_edges
    )

    exposed = _relationship_payloads(
        mock_ingestor, cs.RelationshipType.EXPOSES_ENDPOINT
    )
    legacy_exposed_paths = {
        cast(dict[str, object], rel[3]).get("route_path")
        for rel in exposed
        if rel[0]
        == (
            cs.NodeLabel.MODULE,
            cs.KEY_QUALIFIED_NAME,
            "fastapi_legacy_comp.src.api.routes.legacy",
        )
    }
    assert "/users" in legacy_exposed_paths
    assert "/tax-declarations" in legacy_exposed_paths


def test_materializes_factory_returned_router_calls(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = temp_repo / "fastapi_factory_comp"
    project.mkdir()

    _write(
        project / "main.py",
        """from fastapi import APIRouter, FastAPI

admin = APIRouter()


@admin.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def build_api() -> APIRouter:
    router = APIRouter()
    router.include_router(admin, prefix="/internal")
    return router


app = FastAPI()
app.include_router(build_api(), prefix="/api")
""",
    )

    run_updater(project, mock_ingestor)

    endpoint_nodes = _endpoint_props(mock_ingestor)
    assert any(
        props.get(cs.KEY_ROUTE_PATH) == "/api/internal/health"
        for props in endpoint_nodes
    )

    mounts = _relationship_payloads(mock_ingestor, cs.RelationshipType.MOUNTS_ROUTER)
    assert any(
        rel[0]
        == (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, "fastapi_factory_comp.main")
        and cast(dict[str, object], rel[3]).get("composition_kind") == "factory_call"
        and cast(dict[str, object], rel[3]).get("source_expression") == "build_api()"
        for rel in mounts
    )
