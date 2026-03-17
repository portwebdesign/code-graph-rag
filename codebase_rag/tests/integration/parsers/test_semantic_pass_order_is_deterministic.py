from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from codebase_rag.core import constants as cs
from codebase_rag.graph_db.graph_updater import GraphUpdater
from codebase_rag.infrastructure.parser_loader import load_parsers
from codebase_rag.services.graph_service import MemgraphIngestor
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    build_mock_graph_snapshot,
)

pytestmark = [pytest.mark.integration]


SEMANTIC_NODE_LABELS = {
    cs.NodeLabel.ENDPOINT,
    cs.NodeLabel.DEPENDENCY_PROVIDER,
    cs.NodeLabel.AUTH_POLICY,
    cs.NodeLabel.AUTH_SCOPE,
    cs.NodeLabel.CONTRACT,
    cs.NodeLabel.CONTRACT_FIELD,
    cs.NodeLabel.EVENT_FLOW,
    cs.NodeLabel.QUEUE,
    cs.NodeLabel.TRANSACTION_BOUNDARY,
    cs.NodeLabel.SIDE_EFFECT,
    cs.NodeLabel.TEST_SUITE,
    cs.NodeLabel.TEST_CASE,
    cs.NodeLabel.ENV_VAR,
    cs.NodeLabel.FEATURE_FLAG,
    cs.NodeLabel.SECRET_REF,
}
SEMANTIC_RELATIONSHIP_TYPES = {
    cs.RelationshipType.USES_DEPENDENCY,
    cs.RelationshipType.SECURED_BY,
    cs.RelationshipType.REQUIRES_SCOPE,
    cs.RelationshipType.ACCEPTS_CONTRACT,
    cs.RelationshipType.RETURNS_CONTRACT,
    cs.RelationshipType.DECLARES_FIELD,
    cs.RelationshipType.WRITES_OUTBOX,
    cs.RelationshipType.PUBLISHES_EVENT,
    cs.RelationshipType.CONSUMES_EVENT,
    cs.RelationshipType.USES_HANDLER,
    cs.RelationshipType.USES_QUEUE,
    cs.RelationshipType.BEGINS_TRANSACTION,
    cs.RelationshipType.COMMITS_TRANSACTION,
    cs.RelationshipType.PERFORMS_SIDE_EFFECT,
    cs.RelationshipType.WITHIN_TRANSACTION,
    cs.RelationshipType.BEFORE,
    cs.RelationshipType.AFTER,
    cs.RelationshipType.TESTS_SYMBOL,
    cs.RelationshipType.ASSERTS_CONTRACT,
    cs.RelationshipType.READS_ENV,
    cs.RelationshipType.GATES_CODE_PATH,
}


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _run_full_reparse(project: Path, ingestor: object) -> None:
    parsers, queries = load_parsers()
    GraphUpdater(
        ingestor=ingestor,
        repo_path=project,
        parsers=parsers,
        queries=queries,
        force_full_reparse=True,
    ).run()


def _write_mixed_semantic_repo(project: Path) -> None:
    _write(
        project / ".env",
        """APP_SECRET=super-secret
FEATURE_BILLING=1
""",
    )
    _write(
        project / "models.py",
        """from pydantic import BaseModel


class InvoiceCreate(BaseModel):
    customer_id: str


class InvoiceView(BaseModel):
    id: str
""",
    )
    _write(
        project / "api.py",
        """from fastapi import APIRouter, Depends, Security

from .models import InvoiceCreate, InvoiceView

router = APIRouter()


def get_tenant() -> str:
    return "tenant-1"


def get_actor() -> str:
    return "user-1"


@router.post("/invoices", response_model=InvoiceView, dependencies=[Depends(get_tenant)])
async def create_invoice(
    payload: InvoiceCreate,
    actor: str = Security(get_actor, scopes=["invoices:write"]),
) -> InvoiceView:
    return InvoiceView(id="inv-1")
""",
    )
    _write(
        project / "events.py",
        """def consumer(event: str, queue: str):
    def decorator(fn):
        return fn
    return decorator


class Outbox:
    def publish(self, event: str, payload: dict[str, object], stream: str) -> None:
        return None


outbox = Outbox()


def persist_outbox(invoice_id: str) -> None:
    outbox.publish("invoice.created", {"invoice_id": invoice_id}, stream="invoice-events")


class InvoiceWorker:
    @consumer("invoice.created", queue="invoice-events")
    def handle_invoice_created(self, message: dict[str, object]) -> None:
        return None
""",
    )
    _write(
        project / "transactions.py",
        """class Session:
    def begin(self):
        return self

    def commit(self):
        return None


class Outbox:
    def save(self, event_name: str, payload: dict[str, object]) -> None:
        return None


session = Session()
outbox = Outbox()


def persist_invoice(db) -> None:
    tx = session.begin()
    db.insert({"id": "inv-1"})
    outbox.save("invoice.created", {"id": "inv-1"})
    tx.commit()
""",
    )
    _write(
        project / "settings.py",
        """import os


def read_secret() -> str | None:
    return os.getenv("APP_SECRET")


def billing_enabled() -> bool:
    return os.getenv("FEATURE_BILLING") == "1"
""",
    )
    _write(
        project / "tests/test_api.py",
        """from models import InvoiceCreate


def test_create_invoice_contract():
    payload = InvoiceCreate(customer_id="cus-1")
    assert payload.customer_id == "cus-1"
""",
    )


def test_semantic_pass_order_is_deterministic(
    temp_repo: Path,
    mock_ingestor,
) -> None:
    project = temp_repo / "semantic_pass_order_fixture"
    project.mkdir()
    _write_mixed_semantic_repo(project)

    second_ingestor = MagicMock(spec=MemgraphIngestor)

    _run_full_reparse(project, mock_ingestor)
    first_snapshot = build_mock_graph_snapshot(
        mock_ingestor,
        node_labels={str(label) for label in SEMANTIC_NODE_LABELS},
        relationship_types={str(rel) for rel in SEMANTIC_RELATIONSHIP_TYPES},
    )

    _run_full_reparse(project, second_ingestor)
    second_snapshot = build_mock_graph_snapshot(
        second_ingestor,
        node_labels={str(label) for label in SEMANTIC_NODE_LABELS},
        relationship_types={str(rel) for rel in SEMANTIC_RELATIONSHIP_TYPES},
    )

    assert first_snapshot == second_snapshot

    node_labels = {
        str(node["label"]) for node in first_snapshot["nodes"] if "label" in node
    }
    assert str(cs.NodeLabel.CONTRACT) in node_labels
    assert str(cs.NodeLabel.EVENT_FLOW) in node_labels
    assert str(cs.NodeLabel.TRANSACTION_BOUNDARY) in node_labels
    assert str(cs.NodeLabel.TEST_CASE) in node_labels
    assert str(cs.NodeLabel.ENV_VAR) in node_labels
