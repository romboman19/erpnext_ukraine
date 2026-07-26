"""Versioned third-party-to-OWN conversion commands."""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import getdate

from ...integrations.ownership_conversions import create_ownership_conversion
from ...services.ownership_conversion import (
    OwnershipConversionError,
    OwnershipConversionRequest,
)
from .common import (
    MANAGER_ROLES,
    OPERATOR_ROLES,
    assert_permission,
    assert_roles,
    parse_decimal,
    parse_json,
)


def _payload(document: Any) -> dict[str, Any]:
    return {
        "name": document.name,
        "docstatus": document.docstatus,
        "status": document.status,
        "source_lot": document.source_lot,
        "qty": document.qty,
        "source_method": document.source_method,
        "source_issue": document.source_issue,
        "own_receipt": document.own_receipt,
        "target_lot": document.target_lot,
        "purchase_invoice": document.purchase_invoice,
    }


def _serials(value: str | list[str]) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(row).strip() for row in value if str(row).strip())
    text = str(value or "").strip()
    if not text:
        return ()
    if text.startswith("["):
        try:
            parsed = parse_json(text, label="serial_numbers")
        except ValueError as exc:
            raise OwnershipConversionError(str(exc)) from exc
        if not isinstance(parsed, list):
            raise OwnershipConversionError(
                "serial_numbers must be a JSON list or newline-separated text"
            )
        return tuple(str(row).strip() for row in parsed if str(row).strip())
    return tuple(row.strip() for row in text.splitlines() if row.strip())


@frappe.whitelist(methods=["POST"])
def create(
    *,
    idempotency_key: str,
    posting_date: str,
    posting_time: str,
    source_lot: str,
    qty: str | int | float,
    source_method: str,
    unit_cost: str | int | float,
    currency: str,
    exchange_rate: str | int | float,
    reason: str,
    due_date: str = "",
    supplier_invoice_no: str = "",
    supplier_invoice_date: str = "",
    serial_numbers: str | list[str] = "",
    target_batch_no: str = "",
) -> dict[str, Any]:
    assert_roles(OPERATOR_ROLES)
    assert_permission("CC Ownership Conversion", "create")
    assert_permission("CC Stock Lot", "read", source_lot)
    try:
        parsed_qty = parse_decimal(qty, label="qty")
        parsed_cost = parse_decimal(unit_cost, label="unit_cost")
        parsed_rate = parse_decimal(exchange_rate, label="exchange_rate")
    except ValueError as exc:
        raise OwnershipConversionError(str(exc)) from exc
    document = create_ownership_conversion(
        OwnershipConversionRequest(
            idempotency_key=idempotency_key,
            posting_date=getdate(posting_date),
            posting_time=posting_time,
            source_lot=source_lot,
            qty=parsed_qty,
            source_method=source_method,
            unit_cost=parsed_cost,
            currency=currency,
            exchange_rate=parsed_rate,
            reason=reason,
            due_date=getdate(due_date) if due_date else None,
            supplier_invoice_no=supplier_invoice_no,
            supplier_invoice_date=(
                getdate(supplier_invoice_date) if supplier_invoice_date else None
            ),
            serial_numbers=_serials(serial_numbers),
            target_batch_no=target_batch_no,
        )
    )
    return _payload(document)


@frappe.whitelist(methods=["POST"])
def submit(*, ownership_conversion: str) -> dict[str, Any]:
    assert_roles(OPERATOR_ROLES)
    assert_permission("CC Ownership Conversion", "submit", ownership_conversion)
    document = frappe.get_doc("CC Ownership Conversion", ownership_conversion)
    if document.docstatus == 2:
        frappe.throw("Cancelled CC Ownership Conversion cannot be submitted")
    if document.docstatus == 0:
        document.flags.ignore_permissions = True
        document.submit()
    return _payload(document)


@frappe.whitelist(methods=["POST"])
def cancel(*, ownership_conversion: str) -> dict[str, Any]:
    assert_roles(MANAGER_ROLES)
    assert_permission("CC Ownership Conversion", "cancel", ownership_conversion)
    document = frappe.get_doc("CC Ownership Conversion", ownership_conversion)
    if document.docstatus == 1:
        document.flags.ignore_permissions = True
        document.cancel()
    return _payload(document)
