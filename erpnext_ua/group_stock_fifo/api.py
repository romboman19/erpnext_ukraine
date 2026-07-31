"""Versioned API surface (§27.1).

Everything that writes goes through the reservation service rather than through
document CRUD, because §13.1's transaction boundary is a property of the call,
not of the row it happens to produce.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

import frappe

from .services.allocations import (
    consume_allocation,
    release_allocation,
    reserve,
)
from .services.candidates import GSFLayerCandidateAdapter
from .services.domain import GSFError
from .services.readiness import as_dict as readiness_payload
from .services.reservation import ReservationRequest

OPERATOR_ROLES = ("System Manager", "GSF System Manager", "GSF Stock Manager", "GSF Stock User")
AUDIT_ROLES = ("System Manager", "GSF System Manager", "GSF Auditor")


@frappe.whitelist()
def diagnostics_readiness() -> dict[str, Any]:
    """`GET /diagnostics/readiness` (§27.1)."""
    frappe.only_for(AUDIT_ROLES)
    return readiness_payload()


@frappe.whitelist()
def diagnostics_integrity(company_group: str | None = None) -> dict[str, Any]:
    """`GET /diagnostics/integrity` — the §31 Financial Integrity report."""
    frappe.only_for(AUDIT_ROLES)
    from .services.integrity import check

    return check(company_group).as_dict()


@frappe.whitelist(methods=["POST"])
def allocation_preview(
    *,
    company_group: str,
    physical_location: str,
    seller_company: str,
    item_code: str,
    qty: str | int | float,
    allowed_warehouses: str | list[str] | None = None,
    serial_no: str | None = None,
    batch_no: str | None = None,
) -> dict[str, Any]:
    """`POST /allocation/preview` — what would be taken, without taking it."""
    frappe.only_for(OPERATOR_ROLES)
    from erpnext_ua.consignment_and_commission.services.allocation import AllocationError
    from erpnext_ua.consignment_and_commission.services.candidates import (
        CandidateQuery,
        preview_from_adapters,
    )

    from .services.candidates import source_warehouses

    pools = source_warehouses(
        company_group=company_group, physical_location=physical_location
    )
    warehouses = _warehouse_set(allowed_warehouses) or frozenset(pools)
    adapter = GSFLayerCandidateAdapter(
        company_group=company_group, physical_location=physical_location
    )
    query = CandidateQuery(
        item_code=item_code,
        company=seller_company,
        location=physical_location,
        allowed_warehouses=warehouses & frozenset(pools),
        serial_no=serial_no,
        batch_no=batch_no,
    )
    try:
        slices = preview_from_adapters([adapter], query=query, qty=_decimal(qty, "qty"))
    except AllocationError as error:
        raise GSFError(str(error), "INSUFFICIENT_GLOBAL_STOCK") from error
    return {
        "seller_company": seller_company,
        "slices": [
            {
                "sequence": row.sequence,
                "stock_layer": row.lot_name,
                "source_company": pools.get(row.warehouse),
                "source_warehouse": row.warehouse,
                "qty": float(row.qty),
                "original_fifo_datetime": row.fifo_datetime,
                "requires_reallocation": pools.get(row.warehouse) != seller_company,
            }
            for row in slices
        ],
    }


@frappe.whitelist(methods=["POST"])
def allocation_reserve(
    *,
    idempotency_key: str,
    company_group: str,
    physical_location: str,
    seller_company: str,
    item_code: str,
    qty: str | int | float,
    allowed_warehouses: str | list[str] | None = None,
    serial_no: str | None = None,
    batch_no: str | None = None,
    item_policy: str | None = None,
    external_row_id: str | None = None,
    posting_date: str | None = None,
    checkout: str | None = None,
) -> dict[str, Any]:
    """`POST /allocation/reserve` — hold exact slices, all or nothing."""
    frappe.only_for(OPERATOR_ROLES)
    from .services.candidates import source_warehouses

    warehouses = _warehouse_set(allowed_warehouses) or frozenset(
        source_warehouses(company_group=company_group, physical_location=physical_location)
    )
    allocation = reserve(
        ReservationRequest(
            idempotency_key=idempotency_key,
            company_group=company_group,
            physical_location=physical_location,
            seller_company=seller_company,
            item_code=item_code,
            qty=_decimal(qty, "qty"),
            allowed_warehouses=warehouses,
            serial_no=serial_no,
            batch_no=batch_no,
            item_policy=item_policy,
            external_row_id=external_row_id,
            posting_date=posting_date,
            checkout=checkout,
        )
    )
    return allocation_payload(allocation)


@frappe.whitelist(methods=["POST"])
def checkout_open(
    *,
    idempotency_key: str,
    company_group: str,
    physical_location: str,
    seller_company: str,
    customer: str,
    lines: str | list[dict[str, Any]],
    external_order_doctype: str | None = None,
    external_order_name: str | None = None,
    requires_fiscalization: str | int | bool = 0,
) -> dict[str, Any]:
    """`POST /checkout/open` — record the basket, then walk it as far as it goes."""
    frappe.only_for(OPERATOR_ROLES)
    from .services.checkout import CheckoutLine, CheckoutRequest, open_checkout, run

    parsed = json.loads(lines) if isinstance(lines, str) else lines
    if not isinstance(parsed, list):
        raise GSFError("lines must be a JSON list", "MANUAL_REVIEW_REQUIRED")

    checkout = open_checkout(
        CheckoutRequest(
            idempotency_key=idempotency_key,
            company_group=company_group,
            physical_location=physical_location,
            seller_company=seller_company,
            customer=customer,
            external_order_doctype=external_order_doctype,
            external_order_name=external_order_name,
            requires_fiscalization=bool(int(requires_fiscalization or 0)),
            lines=tuple(
                CheckoutLine(
                    item_code=str(row["item_code"]),
                    qty=_decimal(row["qty"], "qty"),
                    rate=_decimal(row["rate"], "rate"),
                    external_row_id=row.get("external_row_id"),
                )
                for row in parsed
            ),
        )
    )
    return checkout_payload(run(checkout.name))


@frappe.whitelist(methods=["POST"])
def checkout_resume(*, checkout: str) -> dict[str, Any]:
    """`POST /checkout/resume` — carry on from wherever this one stopped."""
    frappe.only_for(OPERATOR_ROLES)
    from .services.checkout import run

    return checkout_payload(run(checkout))


@frappe.whitelist(methods=["POST"])
def checkout_abort(*, checkout: str, reason: str = "aborted by request") -> dict[str, Any]:
    """`POST /checkout/abort` — release or compensate, whichever the state owes."""
    frappe.only_for(OPERATOR_ROLES)
    from .services.checkout import abort

    return checkout_payload(abort(checkout, reason=reason))


def checkout_payload(checkout: Any) -> dict[str, Any]:
    return {
        "name": checkout.name,
        "status": checkout.status,
        "stock_state": checkout.stock_state,
        "erp_sale_state": checkout.erp_sale_state,
        "fiscal_state": checkout.fiscal_state,
        "sales_invoice": checkout.sales_invoice,
        "staging_lane": checkout.staging_lane,
        "failure_code": checkout.failure_code,
        "lines": [
            {
                "item_code": line.item_code,
                "qty": line.qty,
                "rate": line.rate,
                "allocation": line.allocation,
            }
            for line in checkout.lines
        ],
    }


@frappe.whitelist(methods=["POST"])
def allocation_release(*, allocation: str, reason: str = "released by request") -> dict[str, Any]:
    """`POST /allocation/release`."""
    frappe.only_for(OPERATOR_ROLES)
    return allocation_payload(release_allocation(allocation, reason=reason))


@frappe.whitelist(methods=["POST"])
def allocation_consume(
    *, allocation: str, consumer_doctype: str, consumer_document: str
) -> dict[str, Any]:
    """`POST /allocation/consume` — the stock has actually left."""
    frappe.only_for(OPERATOR_ROLES)
    return allocation_payload(
        consume_allocation(
            allocation, consumer_doctype=consumer_doctype, consumer_document=consumer_document
        )
    )


def allocation_payload(allocation: Any) -> dict[str, Any]:
    return {
        "name": allocation.name,
        "status": allocation.status,
        "seller_company": allocation.seller_company,
        "item_code": allocation.item_code,
        "requested_qty": allocation.requested_qty,
        "allocated_qty": allocation.allocated_qty,
        "reserved_at": allocation.reserved_at,
        "expires_at": allocation.expires_at,
        "slices": [
            {
                "sequence": row.sequence,
                "stock_layer": row.stock_layer,
                "source_company": row.source_company,
                "source_warehouse": row.source_warehouse,
                "qty": row.qty,
                "original_fifo_datetime": row.original_fifo_datetime,
                "requires_reallocation": bool(row.requires_reallocation),
            }
            for row in allocation.slices
        ],
    }


def _warehouse_set(value: str | list[str] | None) -> frozenset[str]:
    """An omitted list means "every pool in scope", not "no pools"."""
    if not value:
        return frozenset()
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as error:
            raise GSFError(
                "allowed_warehouses must be a JSON list", "WAREHOUSE_BINDING_MISSING"
            ) from error
    if not isinstance(value, list):
        raise GSFError("allowed_warehouses must be a JSON list", "WAREHOUSE_BINDING_MISSING")
    return frozenset(str(item) for item in value if item)


def _decimal(value: Any, label: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError) as error:
        raise GSFError(f"{label} must be a number", "ALLOCATION_CONFLICT") from error
