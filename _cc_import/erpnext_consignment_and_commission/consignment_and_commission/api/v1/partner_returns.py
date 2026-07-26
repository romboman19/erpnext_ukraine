"""Versioned controlled partner-return commands."""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import getdate

from ...integrations.partner_returns import create_partner_return
from ...services.partner_return import PartnerReturnError, PartnerReturnRequest
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
        "supplier": document.supplier,
        "stock_entry": document.stock_entry,
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
            raise PartnerReturnError(str(exc)) from exc
        if not isinstance(parsed, list):
            raise PartnerReturnError("serial_numbers must be a JSON list or newline-separated text")
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
    reason: str,
    serial_numbers: str | list[str] = "",
) -> dict[str, Any]:
    assert_roles(OPERATOR_ROLES)
    assert_permission("CC Partner Return", "create")
    assert_permission("CC Stock Lot", "read", source_lot)
    try:
        parsed_qty = parse_decimal(qty, label="qty")
    except ValueError as exc:
        raise PartnerReturnError(str(exc)) from exc
    document = create_partner_return(
        PartnerReturnRequest(
            idempotency_key=idempotency_key,
            posting_date=getdate(posting_date),
            posting_time=posting_time,
            source_lot=source_lot,
            qty=parsed_qty,
            reason=reason,
            serial_numbers=_serials(serial_numbers),
        )
    )
    return _payload(document)


@frappe.whitelist(methods=["POST"])
def submit(*, partner_return: str) -> dict[str, Any]:
    assert_roles(OPERATOR_ROLES)
    assert_permission("CC Partner Return", "submit", partner_return)
    document = frappe.get_doc("CC Partner Return", partner_return)
    if document.docstatus == 2:
        frappe.throw("Cancelled CC Partner Return cannot be submitted")
    if document.docstatus == 0:
        document.flags.ignore_permissions = True
        document.submit()
    return _payload(document)


@frappe.whitelist(methods=["POST"])
def cancel(*, partner_return: str) -> dict[str, Any]:
    assert_roles(MANAGER_ROLES)
    assert_permission("CC Partner Return", "cancel", partner_return)
    document = frappe.get_doc("CC Partner Return", partner_return)
    if document.docstatus == 1:
        document.flags.ignore_permissions = True
        document.cancel()
    return _payload(document)
