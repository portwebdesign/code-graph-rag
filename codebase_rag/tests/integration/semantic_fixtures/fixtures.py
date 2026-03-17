from __future__ import annotations

from codebase_rag.tests.integration.semantic_fixtures.helpers import SemanticFixtureSpec

FASTAPI_AUTH_CONTRACT_FIXTURE = SemanticFixtureSpec(
    name="fastapi_semantic_fixture",
    files={
        "main.py": """from fastapi import APIRouter, Depends, FastAPI, Security
from pydantic import BaseModel

app = FastAPI()
router = APIRouter()


class InvoiceCreate(BaseModel):
    customer_id: str
    total_cents: int


class InvoiceResponse(BaseModel):
    id: str
    status: str


def get_tenant() -> str:
    return "tenant-1"


def get_current_user() -> str:
    return "user-1"


@router.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post(
    "/api/invoices",
    response_model=InvoiceResponse,
    dependencies=[Depends(get_tenant)],
    tags=["billing"],
)
async def create_invoice(
    payload: InvoiceCreate,
    actor: str = Security(get_current_user, scopes=["invoices:write"]),
) -> InvoiceResponse:
    return InvoiceResponse(id="inv-1", status="queued")


app.include_router(router)
"""
    },
)

EVENT_FLOW_FIXTURE = SemanticFixtureSpec(
    name="event_flow_semantic_fixture",
    files={
        "main.py": """def consumer(event: str, queue: str, dlq: str | None = None):
    def decorator(fn):
        return fn
    return decorator


class RedisStreamOutbox:
    def publish(self, event: str, payload: dict[str, object], stream: str) -> None:
        return None


class BrokerPublisher:
    def publish(self, event: str, payload: dict[str, object], queue: str) -> None:
        return None


redis_stream_outbox = RedisStreamOutbox()
publisher = BrokerPublisher()


def replay_events(event: str, queue: str, dlq: str) -> None:
    return None


def persist_invoice_outbox(invoice_id: str) -> None:
    redis_stream_outbox.publish("invoice.created", {"invoice_id": invoice_id}, stream="invoice-events")


def dispatch_invoice_created(invoice_id: str) -> None:
    publisher.publish("invoice.created", {"invoice_id": invoice_id}, queue="invoice-events")


class InvoiceWorker:
    @consumer("invoice.created", queue="invoice-events", dlq="invoice-events-dlq")
    def handle_invoice_created(self, message: dict[str, object]) -> None:
        return None


def replay_invoice_created() -> None:
    replay_events("invoice.created", queue="invoice-events", dlq="invoice-events-dlq")
"""
    },
)

EVENT_FLOW_RUNTIME_FIXTURE = SemanticFixtureSpec(
    name="event_flow_runtime_semantic_fixture",
    files={
        "main.py": """def consumer(event: str, queue: str, dlq: str | None = None):
    def decorator(fn):
        return fn
    return decorator


class RedisStreamOutbox:
    def publish(self, event: str, payload: dict[str, object], stream: str) -> None:
        return None


class BrokerPublisher:
    def publish(self, event: str, payload: dict[str, object], queue: str) -> None:
        return None


redis_stream_outbox = RedisStreamOutbox()
publisher = BrokerPublisher()


def replay_events(event: str, queue: str, dlq: str) -> None:
    return None


def persist_invoice_outbox(invoice_id: str) -> None:
    redis_stream_outbox.publish("invoice.created", {"invoice_id": invoice_id}, stream="invoice-events")


def dispatch_invoice_created(invoice_id: str) -> None:
    publisher.publish("invoice.created", {"invoice_id": invoice_id}, queue="invoice-events")


class InvoiceWorker:
    @consumer("invoice.created", queue="invoice-events", dlq="invoice-events-dlq")
    def handle_invoice_created(self, message: dict[str, object]) -> None:
        return None


def replay_invoice_created() -> None:
    replay_events("invoice.created", queue="invoice-events", dlq="invoice-events-dlq")
""",
        "output/runtime/events.ndjson": """{"event_name":"invoice.created","queue":"invoice_events","handler":"InvoiceWorker.handle_invoice_created","stage":"publish"}
{"event_name":"invoice.created","queue":"invoice-events","handler":"InvoiceWorker.handle_invoice_created","stage":"consume","retry_count":2}
{"event_name":"invoice.created","queue":"invoice-events","dlq":"invoice-events-dlq","handler":"InvoiceWorker.handle_invoice_created","stage":"dlq"}
""",
    },
)

TRANSACTION_FLOW_FIXTURE = SemanticFixtureSpec(
    name="transaction_flow_semantic_fixture",
    files={
        "main.py": """from requests import post


class Session:
    def begin(self):
        return self

    def commit(self):
        return None

    def rollback(self):
        return None

    def transaction(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


class Outbox:
    def save(self, name: str, payload: dict[str, object]) -> None:
        return None


class Cache:
    def set(self, key: str, value: str) -> None:
        return None


session = Session()
outbox = Outbox()
cache = Cache()


def persist_invoice(db, graph) -> None:
    tx = session.begin()
    db.insert({"id": "inv-1"})
    outbox.save("invoice.created", {"id": "inv-1"})
    graph.execute("CREATE (:Invoice {id: 'inv-1'})")
    tx.commit()


def persist_with_context(db) -> None:
    with session.transaction():
        db.update({"id": "inv-1"})
        cache.set("invoice:inv-1", "cached")
        post("https://example.com/hooks")


def persist_with_rollback(db) -> None:
    tx = session.begin()
    db.delete({"id": "inv-1"})
    tx.rollback()
""",
    },
)

FRONTEND_CONTRACT_FIXTURE = SemanticFixtureSpec(
    name="frontend_contract_semantic_fixture",
    files={
        "src/app/customers/page.tsx": """import { useQuery } from "@tanstack/react-query";
import { listCustomers } from "@/lib/generated/client";
import { createOrder } from "@/lib/raw/orders";

export default function CustomersPage() {
    const query = useQuery({
        queryKey: ["customers"],
        queryFn: listCustomers,
    });

    async function submit() {
        await createOrder();
    }

    return <button onClick={submit}>{query.data?.length ?? 0}</button>;
}
""",
        "src/lib/contracts.ts": """import { z } from "zod";

export interface Customer {
    id: string;
    name: string;
    loyaltyPoints?: number;
}

export type CreateOrderRequest = {
    customerId: string;
    totalCents: number;
    note?: string;
};

export const OrderResponseSchema = z.object({
    id: z.string(),
    status: z.string(),
    warnings: z.array(z.string()).optional(),
});
""",
        "src/lib/generated/client.ts": """import type { Customer } from "../contracts";

const apiClient = {
    get: async (path: string) => fetch(path),
};

export async function listCustomers(): Promise<Customer[]> {
    return apiClient.get("/api/customers");
}
""",
        "src/lib/raw/orders.ts": """import type { CreateOrderRequest } from "../contracts";
import { z } from "zod";

import { OrderResponseSchema } from "../contracts";

export async function createOrder(
    payload: CreateOrderRequest,
): Promise<z.infer<typeof OrderResponseSchema>> {
    void payload;
    return fetch("/api/orders", { method: "POST" });
}
""",
    },
)

ENV_FLAG_SECRET_FIXTURE = SemanticFixtureSpec(
    name="env_flag_secret_semantic_fixture",
    files={
        ".env": """APP_SECRET=super-secret
FEATURE_BILLING=1
FEATURE_UNUSED=0
NEXT_PUBLIC_API_URL=https://api.example.test
STRIPE_SECRET=sk_live_fixture_secret
""",
        "docker-compose.yml": """services:
  api:
    build: .
    environment:
      APP_SECRET: ${APP_SECRET}
      FEATURE_BILLING: ${FEATURE_BILLING}
      FEATURE_UNUSED: ${FEATURE_UNUSED}
      NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL}
      PUBLIC_CACHE_URL: redis://cache:6379/0
      STRIPE_SECRET: ${STRIPE_SECRET}
""",
        "deployment.yaml": """apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
spec:
  template:
    spec:
      containers:
        - name: api
          image: demo/api:latest
          env:
            - name: APP_SECRET
              valueFrom:
                secretKeyRef:
                  name: api-secrets
                  key: app-secret
            - name: FEATURE_BILLING
              value: "1"
            - name: NEXT_PUBLIC_API_URL
              value: "https://api.example.test"
""",
        "settings.py": """import os

from dotenv import load_dotenv

load_dotenv()


def read_secret() -> str | None:
    return os.getenv("APP_SECRET")


def billing_enabled() -> bool:
    return os.getenv("FEATURE_BILLING") == "1"
""",
        "analytics.py": """import os


def analytics_key() -> str | None:
    return os.getenv("MISSING_ANALYTICS_KEY")
""",
        "payments.py": """import os


def payment_api_key() -> str | None:
    return os.getenv("PAYMENTS_API_KEY")
""",
        "frontend/src/env.ts": """export function frontendApiUrl(): string | undefined {
    return process.env.NEXT_PUBLIC_API_URL;
}

export function billingEnabled(): boolean {
    return process.env.FEATURE_BILLING === "1";
}
""",
        "frontend/src/flags.ts": """export function experimentalBillingEnabled(): boolean {
    return process.env.FEATURE_EXPERIMENTAL === "1";
}
""",
    },
)

OPENAPI_CONTRACT_FIXTURE = SemanticFixtureSpec(
    name="openapi_contract_surface_fixture",
    files={
        "openapi.json": """{
  "openapi": "3.0.3",
  "info": {
    "title": "Orders API",
    "version": "1.0.0"
  },
  "paths": {
    "/api/orders": {
      "post": {
        "requestBody": {
          "required": true,
          "content": {
            "application/json": {
              "schema": {
                "$ref": "#/components/schemas/CreateOrderRequest"
              }
            }
          }
        },
        "responses": {
          "201": {
            "description": "Created",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/OrderResponse"
                }
              }
            }
          }
        }
      }
    },
    "/api/customers": {
      "get": {
        "responses": {
          "200": {
            "description": "OK",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/CustomerListResponse"
                }
              }
            }
          }
        }
      }
    }
  },
  "components": {
    "schemas": {
      "CreateOrderRequest": {
        "type": "object",
        "required": ["customerId", "totalCents"],
        "properties": {
          "customerId": { "type": "string" },
          "totalCents": { "type": "integer" },
          "note": { "type": "string" }
        }
      },
      "OrderResponse": {
        "type": "object",
        "required": ["id", "status"],
        "properties": {
          "id": { "type": "string" },
          "status": { "type": "string" },
          "warnings": { "type": "array", "items": { "type": "string" } }
        }
      },
      "Customer": {
        "type": "object",
        "required": ["id", "name"],
        "properties": {
          "id": { "type": "string" },
          "name": { "type": "string" },
          "loyaltyPoints": { "type": "integer" }
        }
      },
      "CustomerListResponse": {
        "type": "object",
        "required": ["items"],
        "properties": {
          "items": {
            "type": "array",
            "items": { "$ref": "#/components/schemas/Customer" }
          }
        }
      }
    }
  }
}
""",
    },
)

EVENT_RELIABILITY_RISK_FIXTURE = SemanticFixtureSpec(
    name="event_reliability_risk_fixture",
    files={
        "main.py": """from requests import post


def consumer(event: str, queue: str, dlq: str | None = None):
    def decorator(fn):
        return fn
    return decorator


class Session:
    def begin(self):
        return self

    def commit(self):
        return None


class Outbox:
    def save(self, event_name: str, payload: dict[str, object]) -> None:
        return None


class Publisher:
    def publish(self, event: str, payload: dict[str, object], queue: str) -> None:
        return None


session = Session()
outbox = Outbox()
publisher = Publisher()


def replay_events(event: str, queue: str, dlq: str | None = None) -> None:
    return None


def persist_invoice_outbox(invoice_id: str) -> None:
    outbox.save("invoice.created", {"invoice_id": invoice_id})


def dispatch_invoice_created(invoice_id: str) -> None:
    publisher.publish("invoice.created", {"invoice_id": invoice_id}, queue="invoice-events")


def dispatch_invoice_created_again(invoice_id: str) -> None:
    publisher.publish("invoice.created", {"invoice_id": invoice_id}, queue="invoice-events")


def persist_with_external_call_before_commit(db) -> None:
    tx = session.begin()
    db.insert({"id": "inv-1"})
    outbox.save("invoice.created", {"id": "inv-1"})
    post("https://example.com/hooks")
    tx.commit()


class InvoiceWorker:
    @consumer("invoice.created", queue="invoice-events")
    def handle_invoice_created(self, message: dict[str, object]) -> None:
        return None


def replay_invoice_created() -> None:
    replay_events("invoice.created", queue="invoice-events")
""",
    },
)

TEST_SEMANTICS_FIXTURE = SemanticFixtureSpec(
    name="test_semantics_fixture",
    files={
        "app.py": """from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

app = FastAPI()
router = APIRouter()


class OrderCreate(BaseModel):
    customer_id: str


class OrderResponse(BaseModel):
    id: str
    status: str


def persist_order(payload: OrderCreate) -> OrderResponse:
    return OrderResponse(id="ord-1", status="queued")


@router.post("/api/orders", response_model=OrderResponse)
async def create_order(payload: OrderCreate) -> OrderResponse:
    return persist_order(payload)


@router.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(router)
""",
        "src/contracts.ts": """export interface OrderResponse {
  id: string;
  status: string;
}
""",
        "src/client.ts": """export async function createOrder(payload: { customerId: string }) {
  void payload;
  return fetch("/api/orders", { method: "POST" });
}
""",
        "tests/test_orders.py": """from app import OrderCreate, OrderResponse, persist_order


def test_create_order(client):
    result = persist_order(OrderCreate(customer_id="cus-1"))
    assert isinstance(result, OrderResponse)
    response = client.post("/api/orders", json={"customer_id": "cus-1"})
    assert response.status_code == 200
""",
        "tests/test_orders_unittest.py": """import unittest

from app import OrderCreate, persist_order


class TestOrderService(unittest.TestCase):
    def test_persist_order(self):
        result = persist_order(OrderCreate(customer_id="cus-2"))
        self.assertEqual(result.status, "queued")
""",
        "web/orders.spec.ts": """import { expect, test } from "vitest";

import { createOrder } from "../src/client";
import type { OrderResponse } from "../src/contracts";

test("submits order through api", async () => {
  await createOrder({ customerId: "cus-3" });
  await fetch("/api/orders", { method: "POST" });
  const payload = {} as OrderResponse;
  expect(payload).toBeDefined();
});
""",
        "output/runtime/lcov.info": """TN:
SF:app.py
DA:1,1
DA:2,1
DA:3,1
end_of_record
""",
    },
)

QUERY_FINGERPRINT_FIXTURE = SemanticFixtureSpec(
    name="query_fingerprint_semantic_fixture",
    files={
        "main.py": """class Db:
    def execute(self, query: str):
        return query


class Graph:
    def execute(self, query: str):
        return query


db = Db()
graph = Graph()


def load_customer_invoice(customer_id: str):
    sql = '''
    SELECT invoices.id, customers.id
    FROM invoices
    JOIN customers ON customers.id = invoices.customer_id
    WHERE customers.id = 42 AND invoices.status = 'paid'
    '''
    return db.execute(sql)


def mark_invoice_paid(invoice_id: str):
    return db.execute("UPDATE invoices SET status = 'paid' WHERE id = 42")


def read_invoice_graph(invoice_id: str):
    return graph.execute(
        "MATCH (i:Invoice)-[:FOR_CUSTOMER]->(c:Customer) RETURN i, c"
    )


def create_invoice_graph(invoice_id: str):
    query = "CREATE (:Invoice {id: 'inv-1'})"
    return graph.execute(query)
""",
    },
)

FRONTEND_OPERATION_FIXTURE = SemanticFixtureSpec(
    name="frontend_operation_semantic_fixture",
    files={
        "src/app/customers/page.tsx": """import { useQuery } from "@tanstack/react-query";
import { listCustomers } from "@/lib/generated/client";
import { createOrder } from "@/lib/raw/orders";

export default function CustomersPage() {
    const query = useQuery({
        queryKey: ["customers"],
        queryFn: listCustomers,
    });

    async function submit() {
        await createOrder();
    }

    return <button onClick={submit}>{query.data?.length ?? 0}</button>;
}
""",
        "src/lib/generated/client.ts": """type Customer = {
    id: string;
    name: string;
};

const apiClient = {
    get: async (path: string) => fetch(path),
};

export async function listCustomers(): Promise<Customer[]> {
    return apiClient.get("/api/customers");
}
""",
        "src/lib/raw/orders.ts": """export async function createOrder(): Promise<Response> {
    return fetch("/api/orders", { method: "POST" });
}
""",
        "openapi.json": """{
  "openapi": "3.0.3",
  "paths": {
    "/api/customers": {
      "get": {
        "operationId": "listCustomers",
        "responses": {
          "200": {
            "description": "OK"
          }
        }
      }
    },
    "/api/orders": {
      "post": {
        "operationId": "createOrder",
        "responses": {
          "201": {
            "description": "Created"
          }
        }
      }
    }
  }
}
""",
    },
)
