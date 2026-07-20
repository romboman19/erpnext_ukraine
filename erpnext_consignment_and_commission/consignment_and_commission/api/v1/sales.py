"""Versioned authenticated API for allocation-backed managed sales."""

from __future__ import annotations

from typing import Any

import frappe

from ...integrations.reservations import release_allocation, reserve_stock
from ...integrations.sales_invoice import create_sales_invoice_from_allocations
from ...services.reservation import ReservationRequest
from ...services.sale import ManagedSaleError, ManagedSaleLine, ManagedSaleRequest
from ...setup.ownership_dimension import MANAGED_SALE_FIELD
from .common import OPERATOR_ROLES, assert_permission, assert_roles, parse_decimal, parse_json


def _allocation_payload(allocation: Any) -> dict[str, Any]:
    return {
        "name": allocation.name,
        "status": allocation.status,
        "company": allocation.company,
        "location": allocation.location,
        "item_code": allocation.item_code,
        "requested_qty": allocation.requested_qty,
        "reserved_at": allocation.reserved_at,
        "expires_at": allocation.expires_at,
        "slices": [
            {
                "name": row.name,
                "sequence": row.sequence,
                "stock_lot": row.stock_lot,
                "warehouse": row.warehouse,
                "source_method": row.source_method,
                "relationship_model": row.relationship_model,
                "qty": row.qty,
                "serial_no": row.serial_no,
                "batch_no": row.batch_no,
                "fifo_datetime": row.fifo_datetime,
            }
            for row in allocation.slices
        ],
    }


@frappe.whitelist(methods=["POST"])
def reserve(
    *,
    idempotency_key: str,
    item_code: str,
    company: str,
    location: str,
    qty: str | int | float,
    allowed_warehouses: str | list[str],
    serial_no: str | None = None,
    batch_no: str | None = None,
    fiscal_policy: str | None = None,
) -> dict[str, Any]:
    """Reserve exact global FIFO slices as the first write of this request."""
    assert_roles(OPERATOR_ROLES)
    assert_permission("Company", "read", company)
    assert_permission("CC Location", "read", location)
    assert_permission("Item", "read", item_code)
    try:
        warehouses = parse_json(allowed_warehouses, label="allowed_warehouses")
        quantity = parse_decimal(qty, label="qty")
    except ValueError as exc:
        raise ManagedSaleError(str(exc)) from exc
    if not isinstance(warehouses, list):
        raise ManagedSaleError("allowed_warehouses must be a JSON list")
    allocation = reserve_stock(
        ReservationRequest(
            idempotency_key=idempotency_key,
            item_code=item_code,
            company=company,
            location=location,
            qty=quantity,
            allowed_warehouses=frozenset(str(value) for value in warehouses),
            serial_no=serial_no,
            batch_no=batch_no,
            fiscal_policy=fiscal_policy,
        )
    )
    return _allocation_payload(allocation)


@frappe.whitelist(methods=["POST"])
def create_invoice(
    *,
    idempotency_key: str,
    customer: str,
    lines: str | list[dict[str, Any]],
    posting_date: str | None = None,
    currency: str | None = None,
    conversion_rate: str | int | float | None = None,
) -> dict[str, Any]:
    """Create one idempotent draft SI from committed reservation names and rates."""
    assert_roles(OPERATOR_ROLES)
    assert_permission("Sales Invoice", "create")
    assert_permission("Customer", "read", customer)
    try:
        parsed_lines = parse_json(lines, label="lines")
    except ValueError as exc:
        raise ManagedSaleError(str(exc)) from exc
    if not isinstance(parsed_lines, list):
        raise ManagedSaleError("lines must be a JSON list")
    parsed_request_lines = []
    for index, row in enumerate(parsed_lines, start=1):
        if not isinstance(row, dict):
            raise ManagedSaleError("Every sale line must be a JSON object")
        try:
            rate = parse_decimal(row.get("rate"), label=f"lines[{index}].rate")
        except ValueError as exc:
            raise ManagedSaleError(str(exc)) from exc
        parsed_request_lines.append(
            ManagedSaleLine(
                allocation=str(row.get("allocation") or ""),
                rate=rate,
            )
        )
        assert_permission("CC Allocation", "read", str(row.get("allocation") or ""))
    parsed_conversion_rate = None
    if conversion_rate is not None:
        try:
            parsed_conversion_rate = parse_decimal(
                conversion_rate,
                label="conversion_rate",
            )
        except ValueError as exc:
            raise ManagedSaleError(str(exc)) from exc
    request = ManagedSaleRequest(
        idempotency_key=idempotency_key,
        customer=customer,
        posting_date=posting_date,
        lines=tuple(parsed_request_lines),
        currency=currency,
        conversion_rate=parsed_conversion_rate,
    )
    invoice = create_sales_invoice_from_allocations(request)
    return {
        "name": invoice.name,
        "docstatus": invoice.docstatus,
        "grand_total": invoice.grand_total,
        "currency": invoice.currency,
        "allocations": list(dict.fromkeys(line.allocation for line in request.lines)),
    }


@frappe.whitelist(methods=["POST"])
def submit_invoice(*, sales_invoice: str) -> dict[str, Any]:
    """Submit only a managed allocation-backed draft; retries return the submitted SI."""
    assert_roles(OPERATOR_ROLES)
    invoice = frappe.get_doc("Sales Invoice", sales_invoice)
    assert_permission("Sales Invoice", "submit", sales_invoice)
    if not invoice.get(MANAGED_SALE_FIELD):
        frappe.throw("Only a CC managed Sales Invoice can use this endpoint")
    if invoice.docstatus == 2:
        frappe.throw("Cancelled managed Sales Invoice cannot be submitted")
    if invoice.docstatus == 0:
        invoice.flags.ignore_permissions = True
        invoice.submit()
    return {
        "name": invoice.name,
        "docstatus": invoice.docstatus,
        "grand_total": invoice.grand_total,
        "outstanding_amount": invoice.outstanding_amount,
    }


@frappe.whitelist(methods=["POST"])
def release(*, allocation: str, reason: str) -> dict[str, Any]:
    """Release an abandoned, still-reserved allocation idempotently."""
    assert_roles(OPERATOR_ROLES)
    assert_permission("CC Allocation", "read", allocation)
    document = release_allocation(allocation, reason=reason)
    return _allocation_payload(document)
