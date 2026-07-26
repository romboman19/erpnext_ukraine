"""Versioned managed-return commands."""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import getdate

from ...integrations.sale_returns import create_return_invoice
from ...services.sale_return import (
    ManagedReturnError,
    ManagedReturnLine,
    ManagedReturnRequest,
)
from ...setup.ownership_dimension import MANAGED_RETURN_FIELD
from .common import (
    MANAGER_ROLES,
    OPERATOR_ROLES,
    assert_permission,
    assert_roles,
    parse_decimal,
    parse_json,
)


def _payload(invoice: Any) -> dict[str, Any]:
    return {
        "name": invoice.name,
        "docstatus": invoice.docstatus,
        "return_against": invoice.return_against,
        "grand_total": invoice.grand_total,
        "currency": invoice.currency,
    }


@frappe.whitelist(methods=["POST"])
def create(
    *,
    idempotency_key: str,
    posting_date: str,
    lines: str | list[dict[str, Any]],
) -> dict[str, Any]:
    """Create one draft return tied to exact immutable sold allocations."""
    assert_roles(OPERATOR_ROLES)
    assert_permission("Sales Invoice", "create")
    try:
        parsed = parse_json(lines, label="lines")
    except ValueError as exc:
        raise ManagedReturnError(str(exc)) from exc
    if not isinstance(parsed, list):
        raise ManagedReturnError("lines must be a JSON list")
    request_lines = []
    for index, row in enumerate(parsed, start=1):
        if not isinstance(row, dict):
            raise ManagedReturnError("Every return line must be a JSON object")
        try:
            qty = parse_decimal(row.get("qty"), label=f"lines[{index}].qty")
        except ValueError as exc:
            raise ManagedReturnError(str(exc)) from exc
        request_lines.append(
            ManagedReturnLine(
                sale_allocation=str(row.get("sale_allocation") or ""),
                qty=qty,
            )
        )
        assert_permission(
            "CC Sale Allocation",
            "read",
            str(row.get("sale_allocation") or ""),
        )
    invoice = create_return_invoice(
        ManagedReturnRequest(
            idempotency_key=idempotency_key,
            posting_date=getdate(posting_date),
            lines=tuple(request_lines),
        )
    )
    return _payload(invoice)


@frappe.whitelist(methods=["POST"])
def submit(*, sales_invoice: str) -> dict[str, Any]:
    assert_roles(OPERATOR_ROLES)
    assert_permission("Sales Invoice", "submit", sales_invoice)
    invoice = frappe.get_doc("Sales Invoice", sales_invoice)
    if not invoice.get(MANAGED_RETURN_FIELD):
        frappe.throw("Only a CC managed return can use this endpoint")
    if invoice.docstatus == 2:
        frappe.throw("Cancelled managed return cannot be submitted")
    if invoice.docstatus == 0:
        invoice.flags.ignore_permissions = True
        invoice.submit()
    return _payload(invoice)


@frappe.whitelist(methods=["POST"])
def cancel(*, sales_invoice: str) -> dict[str, Any]:
    assert_roles(MANAGER_ROLES)
    assert_permission("Sales Invoice", "cancel", sales_invoice)
    invoice = frappe.get_doc("Sales Invoice", sales_invoice)
    if not invoice.get(MANAGED_RETURN_FIELD):
        frappe.throw("Only a CC managed return can use this endpoint")
    if invoice.docstatus == 1:
        invoice.flags.ignore_permissions = True
        invoice.cancel()
    return _payload(invoice)
