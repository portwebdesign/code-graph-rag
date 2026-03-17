from __future__ import annotations

from codebase_rag.parsers.pipeline.ts_contracts import (
    extract_typescript_contracts,
    extract_typescript_function_contracts,
)


def test_extracts_typescript_interface_type_alias_and_zod_contracts() -> None:
    source = """import { z } from "zod";

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
"""

    contracts = extract_typescript_contracts(source)
    contract_index = {contract.name: contract for contract in contracts}

    assert set(contract_index) == {
        "Customer",
        "CreateOrderRequest",
        "OrderResponseSchema",
    }
    assert contract_index["Customer"].kind == "typescript_interface"
    assert contract_index["CreateOrderRequest"].kind == "typescript_type_alias"
    assert contract_index["OrderResponseSchema"].kind == "zod"

    customer_fields = {field.name: field for field in contract_index["Customer"].fields}
    assert customer_fields["id"].type_repr == "string"
    assert customer_fields["loyaltyPoints"].required is False

    order_fields = {
        field.name: field for field in contract_index["OrderResponseSchema"].fields
    }
    assert order_fields["status"].type_repr == "string"
    assert order_fields["warnings"].type_repr == "string[]"
    assert order_fields["warnings"].required is False


def test_extracts_typescript_function_request_and_response_contracts() -> None:
    source = """import { z } from "zod";
import type { Customer, CreateOrderRequest } from "./contracts";
import { OrderResponseSchema } from "./contracts";

export async function listCustomers(): Promise<Customer[]> {
    return [];
}

export const createOrder = async (
    payload: CreateOrderRequest,
): Promise<z.infer<typeof OrderResponseSchema>> => {
    void payload;
    return { id: "1", status: "queued" };
};
"""

    surfaces = extract_typescript_function_contracts(
        source, {"Customer", "CreateOrderRequest", "OrderResponseSchema"}
    )
    surface_index = {surface.function_name: surface for surface in surfaces}

    assert surface_index["listCustomers"].request_contracts == ()
    assert surface_index["listCustomers"].response_contracts == ("Customer",)
    assert surface_index["createOrder"].request_contracts == ("CreateOrderRequest",)
    assert surface_index["createOrder"].response_contracts == ("OrderResponseSchema",)
