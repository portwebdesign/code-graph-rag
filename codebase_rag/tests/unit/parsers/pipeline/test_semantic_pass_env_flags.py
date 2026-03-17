from __future__ import annotations

from pathlib import Path

from codebase_rag.core import constants as cs
from codebase_rag.tests.conftest import get_nodes, get_relationships, run_updater


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


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
""",
    )
    _write(
        project / "api.py",
        """from fastapi import APIRouter

from .models import InvoiceCreate

router = APIRouter()


@router.post("/invoices")
async def create_invoice(payload: InvoiceCreate) -> dict[str, str]:
    return {"status": "ok"}
""",
    )
    _write(
        project / "events.py",
        """def consumer(event: str, queue: str):
    def decorator(fn):
        return fn
    return decorator


class Publisher:
    def publish(self, event: str, payload: dict[str, object], queue: str) -> None:
        return None


publisher = Publisher()


def dispatch_invoice_created(invoice_id: str) -> None:
    publisher.publish("invoice.created", {"invoice_id": invoice_id}, queue="invoice-events")


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


session = Session()


def persist_invoice(db) -> None:
    tx = session.begin()
    db.insert({"id": "inv-1"})
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
        """from api import create_invoice
from models import InvoiceCreate


def test_create_invoice():
    payload = InvoiceCreate(customer_id="cus-1")
    create_invoice(payload)
    assert payload.customer_id == "cus-1"
""",
    )


def test_graph_updater_can_disable_all_semantic_passes(
    temp_repo: Path,
    mock_ingestor,
    monkeypatch,
) -> None:
    project = temp_repo / "semantic_flags_all_disabled"
    project.mkdir()
    _write_mixed_semantic_repo(project)

    monkeypatch.setenv("CODEGRAPH_CONTRACT_SEMANTICS", "0")
    monkeypatch.setenv("CODEGRAPH_EVENT_FLOW_SEMANTICS", "0")
    monkeypatch.setenv("CODEGRAPH_TRANSACTION_FLOW_SEMANTICS", "0")
    monkeypatch.setenv("CODEGRAPH_CONFIG_SEMANTICS", "0")
    monkeypatch.setenv("CODEGRAPH_TEST_SEMANTICS", "0")

    run_updater(project, mock_ingestor)

    assert not get_nodes(mock_ingestor, cs.NodeLabel.CONTRACT)
    assert not get_nodes(mock_ingestor, cs.NodeLabel.EVENT_FLOW)
    assert not get_nodes(mock_ingestor, cs.NodeLabel.TRANSACTION_BOUNDARY)
    assert not get_nodes(mock_ingestor, cs.NodeLabel.ENV_VAR)
    assert not get_nodes(mock_ingestor, cs.NodeLabel.TEST_CASE)
    assert not get_relationships(mock_ingestor, cs.RelationshipType.ACCEPTS_CONTRACT)
    assert not get_relationships(mock_ingestor, cs.RelationshipType.PUBLISHES_EVENT)
    assert not get_relationships(mock_ingestor, cs.RelationshipType.BEGINS_TRANSACTION)
    assert not get_relationships(mock_ingestor, cs.RelationshipType.READS_ENV)
    assert not get_relationships(mock_ingestor, cs.RelationshipType.TESTS_SYMBOL)


def test_graph_updater_respects_individual_semantic_env_flags(
    temp_repo: Path,
    mock_ingestor,
    monkeypatch,
) -> None:
    project = temp_repo / "semantic_flags_individual"
    project.mkdir()
    _write_mixed_semantic_repo(project)

    monkeypatch.setenv("CODEGRAPH_CONTRACT_SEMANTICS", "1")
    monkeypatch.setenv("CODEGRAPH_EVENT_FLOW_SEMANTICS", "0")
    monkeypatch.setenv("CODEGRAPH_TRANSACTION_FLOW_SEMANTICS", "1")
    monkeypatch.setenv("CODEGRAPH_CONFIG_SEMANTICS", "1")
    monkeypatch.setenv("CODEGRAPH_TEST_SEMANTICS", "1")

    run_updater(project, mock_ingestor)

    assert get_nodes(mock_ingestor, cs.NodeLabel.CONTRACT)
    assert not get_nodes(mock_ingestor, cs.NodeLabel.EVENT_FLOW)
    assert get_nodes(mock_ingestor, cs.NodeLabel.TRANSACTION_BOUNDARY)
    assert get_nodes(mock_ingestor, cs.NodeLabel.ENV_VAR)
    assert get_nodes(mock_ingestor, cs.NodeLabel.TEST_CASE)
    assert get_relationships(mock_ingestor, cs.RelationshipType.ACCEPTS_CONTRACT)
    assert not get_relationships(mock_ingestor, cs.RelationshipType.PUBLISHES_EVENT)
    assert get_relationships(mock_ingestor, cs.RelationshipType.BEGINS_TRANSACTION)
    assert get_relationships(mock_ingestor, cs.RelationshipType.READS_ENV)
    assert get_relationships(mock_ingestor, cs.RelationshipType.TESTS_SYMBOL)
