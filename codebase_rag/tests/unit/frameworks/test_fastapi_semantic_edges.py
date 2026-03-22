from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

from codebase_rag.core import constants as cs
from codebase_rag.tests.conftest import get_nodes, get_relationships, run_updater


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _node_props(mock_ingestor: MagicMock, node_type: str) -> list[dict[str, object]]:
    return [
        cast(dict[str, object], call[0][1])
        for call in get_nodes(mock_ingestor, node_type)
    ]


def test_materializes_fastapi_dependency_policy_and_contract_edges(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = temp_repo / "fastapi_semantics"
    project.mkdir()

    _write(
        project / "main.py",
        """from fastapi import APIRouter, Depends, Security
from pydantic import BaseModel

router = APIRouter()


class InvoiceResponse(BaseModel):
    id: str
    status: str


def get_tenant() -> str:
    return "tenant"


def get_current_user() -> str:
    return "user"


@router.get(
    "/invoices",
    response_model=InvoiceResponse,
    dependencies=[Depends(get_tenant)],
    tags=["billing", "internal"],
)
async def list_invoices(
    user: str = Security(get_current_user, scopes=["invoices:read"])
) -> InvoiceResponse:
    return InvoiceResponse(id="1", status="ok")
""",
    )

    run_updater(project, mock_ingestor)

    dependency_nodes = _node_props(mock_ingestor, cs.NodeLabel.DEPENDENCY_PROVIDER)
    assert any(props.get(cs.KEY_NAME) == "get_tenant" for props in dependency_nodes)

    policy_nodes = _node_props(mock_ingestor, cs.NodeLabel.AUTH_POLICY)
    assert any(props.get(cs.KEY_NAME) == "get_current_user" for props in policy_nodes)

    scope_nodes = _node_props(mock_ingestor, cs.NodeLabel.AUTH_SCOPE)
    assert any(props.get(cs.KEY_NAME) == "invoices:read" for props in scope_nodes)

    contract_nodes = _node_props(mock_ingestor, cs.NodeLabel.CONTRACT)
    assert any(props.get(cs.KEY_NAME) == "InvoiceResponse" for props in contract_nodes)

    endpoint_nodes = _node_props(mock_ingestor, cs.NodeLabel.ENDPOINT)
    endpoint_qn = "fastapi_semantics.endpoint.fastapi.GET:/invoices"
    assert any(
        props.get(cs.KEY_QUALIFIED_NAME) == endpoint_qn
        and props.get("response_model") == "InvoiceResponse"
        for props in endpoint_nodes
    )

    uses_dependency = [
        call.args
        for call in get_relationships(
            mock_ingestor, cs.RelationshipType.USES_DEPENDENCY
        )
    ]
    assert any(
        rel[0] == (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn)
        and rel[2][0] == cs.NodeLabel.DEPENDENCY_PROVIDER
        and cast(dict[str, object], rel[3]).get("dependency_name") == "get_tenant"
        and cast(dict[str, object], rel[3]).get(cs.KEY_SOURCE_PARSER)
        == "framework_linker"
        and cast(dict[str, object], rel[3]).get(cs.KEY_CONFIDENCE) == 0.9
        for rel in uses_dependency
    )

    secured_by = [
        call.args
        for call in get_relationships(mock_ingestor, cs.RelationshipType.SECURED_BY)
    ]
    assert any(
        rel[0] == (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn)
        and rel[2][0] == cs.NodeLabel.AUTH_POLICY
        and cast(dict[str, object], rel[3]).get("policy_name") == "get_current_user"
        for rel in secured_by
    )

    requires_scope = [
        call.args
        for call in get_relationships(mock_ingestor, cs.RelationshipType.REQUIRES_SCOPE)
    ]
    assert any(
        rel[2][0] == cs.NodeLabel.AUTH_SCOPE
        and cast(dict[str, object], rel[3]).get("scope_name") == "invoices:read"
        for rel in requires_scope
    )

    returns_contract = [
        call.args
        for call in get_relationships(
            mock_ingestor, cs.RelationshipType.RETURNS_CONTRACT
        )
    ]
    assert any(
        rel[0] == (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn)
        and rel[2][0] == cs.NodeLabel.CONTRACT
        and cast(dict[str, object], rel[3]).get("contract_name") == "InvoiceResponse"
        for rel in returns_contract
    )

    resolves_to = [
        call.args
        for call in get_relationships(mock_ingestor, cs.RelationshipType.RESOLVES_TO)
    ]
    assert any(
        rel[0]
        == (
            cs.NodeLabel.DEPENDENCY_PROVIDER,
            cs.KEY_QUALIFIED_NAME,
            "fastapi_semantics.semantic.dependency_provider.fastapi_semantics.main.get_tenant",
        )
        and rel[2]
        == (
            cs.NodeLabel.FUNCTION,
            cs.KEY_QUALIFIED_NAME,
            "fastapi_semantics.main.get_tenant",
        )
        for rel in resolves_to
    )
    assert any(
        rel[0]
        == (
            cs.NodeLabel.AUTH_POLICY,
            cs.KEY_QUALIFIED_NAME,
            "fastapi_semantics.semantic.auth_policy.fastapi_semantics.main.get_current_user",
        )
        and rel[2]
        == (
            cs.NodeLabel.FUNCTION,
            cs.KEY_QUALIFIED_NAME,
            "fastapi_semantics.main.get_current_user",
        )
        for rel in resolves_to
    )

    callback_registrations = [
        call.args
        for call in get_relationships(
            mock_ingestor, cs.RelationshipType.REGISTERS_CALLBACK
        )
    ]
    assert any(
        rel[0] == (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn)
        and rel[2]
        == (
            cs.NodeLabel.FUNCTION,
            cs.KEY_QUALIFIED_NAME,
            "fastapi_semantics.main.get_tenant",
        )
        and cast(dict[str, object], rel[3]).get("registration_kind")
        == "fastapi_dependency"
        for rel in callback_registrations
    )
    assert any(
        rel[0]
        == (
            cs.NodeLabel.FUNCTION,
            cs.KEY_QUALIFIED_NAME,
            "fastapi_semantics.main.list_invoices",
        )
        and rel[2]
        == (
            cs.NodeLabel.FUNCTION,
            cs.KEY_QUALIFIED_NAME,
            "fastapi_semantics.main.get_current_user",
        )
        and cast(dict[str, object], rel[3]).get("registration_kind")
        == "fastapi_auth_policy"
        for rel in callback_registrations
    )


def test_unresolved_fastapi_dependencies_become_typed_placeholders(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = temp_repo / "fastapi_semantics_placeholder"
    project.mkdir()

    _write(
        project / "main.py",
        """from fastapi import APIRouter, Depends, Security

router = APIRouter()


@router.get("/health", dependencies=[Depends(resolve_tenant)])
async def health(actor: str = Security(resolve_actor, scopes=["health:read"])) -> dict[str, str]:
    return {"status": "ok"}
""",
    )

    run_updater(project, mock_ingestor)

    provider_nodes = _node_props(mock_ingestor, cs.NodeLabel.DEPENDENCY_PROVIDER)
    assert any(
        props.get(cs.KEY_NAME) == "resolve_tenant"
        and props.get(cs.KEY_IS_PLACEHOLDER) is True
        for props in provider_nodes
    )

    policy_nodes = _node_props(mock_ingestor, cs.NodeLabel.AUTH_POLICY)
    assert any(
        props.get(cs.KEY_NAME) == "resolve_actor"
        and props.get(cs.KEY_IS_PLACEHOLDER) is True
        for props in policy_nodes
    )


def test_auth_like_depends_infers_secured_by_edges(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = temp_repo / "fastapi_depends_auth_inference"
    project.mkdir()

    _write(
        project / "main.py",
        """from fastapi import APIRouter, Depends

router = APIRouter()


def require_system_actor() -> str:
    return "system"


@router.get("/system/status", dependencies=[Depends(require_system_actor)])
async def system_status() -> dict[str, str]:
    return {"status": "ok"}
""",
    )

    run_updater(project, mock_ingestor)

    endpoint_qn = "fastapi_depends_auth_inference.endpoint.fastapi.GET:/system/status"
    secured_by = [
        call.args
        for call in get_relationships(mock_ingestor, cs.RelationshipType.SECURED_BY)
    ]
    assert any(
        rel[0] == (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn)
        and rel[2][0] == cs.NodeLabel.AUTH_POLICY
        and cast(dict[str, object], rel[3]).get("policy_name") == "require_system_actor"
        and cast(dict[str, object], rel[3]).get("inferred_from_dependency") is True
        for rel in secured_by
    )


def test_nested_fastapi_dependency_bindings_register_real_functions(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = temp_repo / "fastapi_nested_dependencies"
    project.mkdir()

    _write(
        project / "main.py",
        """from fastapi import APIRouter, Depends

router = APIRouter()


def get_current_principal() -> str:
    return "principal"


def require_authenticated_principal(
    principal: str = Depends(get_current_principal),
) -> str:
    return principal


@router.get("/secure", dependencies=[Depends(require_authenticated_principal)])
async def secure_status() -> dict[str, str]:
    return {"status": "ok"}
""",
    )

    run_updater(project, mock_ingestor)

    callback_registrations = [
        call.args
        for call in get_relationships(
            mock_ingestor, cs.RelationshipType.REGISTERS_CALLBACK
        )
    ]
    assert any(
        rel[0]
        == (
            cs.NodeLabel.FUNCTION,
            cs.KEY_QUALIFIED_NAME,
            "fastapi_nested_dependencies.main.require_authenticated_principal",
        )
        and rel[2]
        == (
            cs.NodeLabel.FUNCTION,
            cs.KEY_QUALIFIED_NAME,
            "fastapi_nested_dependencies.main.get_current_principal",
        )
        and cast(dict[str, object], rel[3]).get("registration_kind")
        == "fastapi_dependency"
        for rel in callback_registrations
    )

    resolves_to = [
        call.args
        for call in get_relationships(mock_ingestor, cs.RelationshipType.RESOLVES_TO)
    ]
    assert any(
        rel[0]
        == (
            cs.NodeLabel.DEPENDENCY_PROVIDER,
            cs.KEY_QUALIFIED_NAME,
            "fastapi_nested_dependencies.semantic.dependency_provider.fastapi_nested_dependencies.main.get_current_principal",
        )
        and rel[2]
        == (
            cs.NodeLabel.FUNCTION,
            cs.KEY_QUALIFIED_NAME,
            "fastapi_nested_dependencies.main.get_current_principal",
        )
        for rel in resolves_to
    )


def test_fastapi_app_callbacks_and_module_local_dependency_resolution_prefer_same_module(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = temp_repo / "fastapi_callback_resolution"
    project.mkdir()

    _write(
        project / "helpers.py",
        """def build_operation_id() -> str:
    return "external"


def get_ai_graph_service() -> str:
    return "external"
""",
    )
    _write(
        project / "main.py",
        """from fastapi import FastAPI, APIRouter, Depends

from .helpers import build_operation_id, get_ai_graph_service as imported_ai_graph_service

app = FastAPI(generate_unique_id_function=build_operation_id)
router = APIRouter()


def build_operation_id(route=None) -> str:
    return "local"


def get_ai_graph_service() -> str:
    return "local"


@router.get("/ai")
async def ai_status(service: str = Depends(get_ai_graph_service)) -> dict[str, str]:
    return {"service": service, "shadow": imported_ai_graph_service()}


app.include_router(router)
""",
    )

    run_updater(project, mock_ingestor)

    callback_registrations = [
        call.args
        for call in get_relationships(
            mock_ingestor, cs.RelationshipType.REGISTERS_CALLBACK
        )
    ]
    assert any(
        rel[0]
        == (
            cs.NodeLabel.MODULE,
            cs.KEY_QUALIFIED_NAME,
            "fastapi_callback_resolution.main",
        )
        and rel[2]
        == (
            cs.NodeLabel.FUNCTION,
            cs.KEY_QUALIFIED_NAME,
            "fastapi_callback_resolution.main.build_operation_id",
        )
        and cast(dict[str, object], rel[3]).get("registration_kind")
        == "fastapi_app_callback"
        for rel in callback_registrations
    )
    assert any(
        rel[2]
        == (
            cs.NodeLabel.FUNCTION,
            cs.KEY_QUALIFIED_NAME,
            "fastapi_callback_resolution.main.get_ai_graph_service",
        )
        and cast(dict[str, object], rel[3]).get("dependency_name")
        == "get_ai_graph_service"
        for rel in callback_registrations
    )


def test_materializes_fastapi_websocket_endpoint_and_handler_edges(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = temp_repo / "fastapi_websocket_semantics"
    project.mkdir()

    _write(
        project / "main.py",
        """from fastapi import APIRouter, WebSocket

router = APIRouter()


@router.websocket("/ws/events")
async def stream_events(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_text("ok")
""",
    )

    run_updater(project, mock_ingestor)

    endpoint_qn = "fastapi_websocket_semantics.endpoint.fastapi.WEBSOCKET:/ws/events"
    endpoint_nodes = _node_props(mock_ingestor, cs.NodeLabel.ENDPOINT)
    assert any(
        props.get(cs.KEY_QUALIFIED_NAME) == endpoint_qn
        and props.get(cs.KEY_HTTP_METHOD) == "WEBSOCKET"
        and props.get(cs.KEY_ROUTE_PATH) == "/ws/events"
        for props in endpoint_nodes
    )

    has_endpoint = [
        call.args
        for call in get_relationships(mock_ingestor, cs.RelationshipType.HAS_ENDPOINT)
    ]
    assert any(
        rel[2] == (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn)
        and str(rel[0][2]).endswith(".stream_events")
        for rel in has_endpoint
    )

    routes_to_action = [
        call.args
        for call in get_relationships(
            mock_ingestor, cs.RelationshipType.ROUTES_TO_ACTION
        )
    ]
    assert any(
        rel[0] == (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn)
        and str(rel[2][2]).endswith(".stream_events")
        for rel in routes_to_action
    )


def test_contract_semantics_pass_emits_request_edges_and_field_graph(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = temp_repo / "fastapi_contracts"
    project.mkdir()

    _write(
        project / "models.py",
        """from dataclasses import dataclass
from typing import TypedDict

from pydantic import BaseModel


class InvoiceCreate(BaseModel):
    customer_id: str
    total_cents: int = 0


@dataclass
class InvoiceView:
    id: str
    status: str


class InvoicePatch(TypedDict, total=False):
    status: str
    note: str
""",
    )
    _write(
        project / "api.py",
        """from fastapi import APIRouter

from .models import InvoiceCreate, InvoiceView

router = APIRouter()


@router.post("/invoices", response_model=InvoiceView)
async def create_invoice(payload: InvoiceCreate) -> InvoiceView:
    return InvoiceView(id="1", status="queued")
""",
    )

    run_updater(project, mock_ingestor)

    contract_nodes = _node_props(mock_ingestor, cs.NodeLabel.CONTRACT)
    assert any(
        props.get(cs.KEY_NAME) == "InvoiceCreate"
        and props.get("contract_kind") == "pydantic"
        and props.get(cs.KEY_SOURCE_PARSER) == "contract_semantics_pass"
        for props in contract_nodes
    )
    assert any(
        props.get(cs.KEY_NAME) == "InvoiceView"
        and props.get("contract_kind") == "dataclass"
        for props in contract_nodes
    )
    assert any(
        props.get(cs.KEY_NAME) == "InvoicePatch"
        and props.get("contract_kind") == "typeddict"
        for props in contract_nodes
    )

    field_nodes = _node_props(mock_ingestor, cs.NodeLabel.CONTRACT_FIELD)
    assert any(
        props.get(cs.KEY_NAME) == "customer_id"
        and props.get("field_type") == "str"
        and props.get("required") is True
        for props in field_nodes
    )
    assert any(
        props.get(cs.KEY_NAME) == "total_cents"
        and props.get("field_type") == "int"
        and props.get("required") is False
        for props in field_nodes
    )
    assert any(
        props.get(cs.KEY_NAME) == "status" and props.get("required") is False
        for props in field_nodes
    )

    endpoint_qn = "fastapi_contracts.endpoint.fastapi.POST:/invoices"
    accepts_contract = [
        call.args
        for call in get_relationships(
            mock_ingestor, cs.RelationshipType.ACCEPTS_CONTRACT
        )
    ]
    assert any(
        rel[0] == (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn)
        and rel[2][0] == cs.NodeLabel.CONTRACT
        and cast(dict[str, object], rel[3]).get("contract_name") == "InvoiceCreate"
        and cast(dict[str, object], rel[3]).get(cs.KEY_SOURCE_PARSER)
        == "contract_semantics_pass"
        for rel in accepts_contract
    )

    declares_field = [
        call.args
        for call in get_relationships(mock_ingestor, cs.RelationshipType.DECLARES_FIELD)
    ]
    assert any(
        rel[0][0] == cs.NodeLabel.CONTRACT
        and rel[2][0] == cs.NodeLabel.CONTRACT_FIELD
        and cast(dict[str, object], rel[3]).get("field_name") == "customer_id"
        for rel in declares_field
    )


def test_contract_semantics_can_be_disabled_with_env_flag(
    temp_repo: Path,
    mock_ingestor: MagicMock,
    monkeypatch,
) -> None:
    project = temp_repo / "fastapi_contracts_disabled"
    project.mkdir()

    _write(
        project / "main.py",
        """from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class InvoiceCreate(BaseModel):
    customer_id: str


@router.post("/invoices")
async def create_invoice(payload: InvoiceCreate) -> dict[str, str]:
    return {"status": "ok"}
""",
    )

    monkeypatch.setenv("CODEGRAPH_CONTRACT_SEMANTICS", "0")
    run_updater(project, mock_ingestor)

    assert not get_nodes(mock_ingestor, cs.NodeLabel.CONTRACT_FIELD)
    assert not get_relationships(mock_ingestor, cs.RelationshipType.ACCEPTS_CONTRACT)
