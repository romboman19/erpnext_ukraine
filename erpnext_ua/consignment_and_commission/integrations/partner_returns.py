"""Controlled zero-value return of exact third-party stock to its partner."""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from typing import Any

from ..services.partner_return import (
    PartnerReturnError,
    PartnerReturnRequest,
    canonical_posting_time,
    partner_return_fingerprint,
    validate_partner_return_request,
)
from ..services.stock_lot import get_ownership_balance
from ..setup.ownership_dimension import OWNERSHIP_FIELD, PARTNER_RETURN_FIELD

CANCELLATION_FLAG = "cc_partner_return_cancellation"
BACKLINK_DOCTYPES = {"CC Partner Return"}


def _existing_partner_return(frappe: Any, request: PartnerReturnRequest, fingerprint: str) -> Any | None:
    name = frappe.db.get_value(
        "CC Partner Return",
        {"idempotency_key": request.idempotency_key},
        "name",
    )
    if not name:
        return None
    document = frappe.get_doc("CC Partner Return", name)
    if document.request_fingerprint != fingerprint:
        raise PartnerReturnError(
            f"Partner return idempotency key {request.idempotency_key!r} belongs to another request"
        )
    return document


def create_partner_return(request: PartnerReturnRequest) -> Any:
    """Create or replay one exact draft return without duplicating stock movement."""
    import frappe

    validate_partner_return_request(request)
    fingerprint = partner_return_fingerprint(request)
    existing = _existing_partner_return(frappe, request, fingerprint)
    if existing:
        return existing
    document = frappe.get_doc(
        {
            "doctype": "CC Partner Return",
            "idempotency_key": request.idempotency_key,
            "request_fingerprint": fingerprint,
            "posting_date": request.posting_date,
            "posting_time": canonical_posting_time(request.posting_time),
            "source_lot": request.source_lot,
            "qty": request.qty,
            "reason": request.reason,
            "serial_numbers": "\n".join(request.serial_numbers),
        }
    )
    savepoint = "cc_partner_return_create"
    frappe.db.savepoint(savepoint)
    try:
        document.insert(ignore_permissions=True)
    except frappe.DuplicateEntryError:
        frappe.db.rollback(save_point=savepoint)
        existing = _existing_partner_return(frappe, request, fingerprint)
        if existing:
            return existing
        raise
    return document


def _material_issue_type(frappe: Any) -> str:
    value = frappe.db.get_value(
        "Stock Entry Type",
        {"purpose": "Material Issue", "is_standard": 1},
        "name",
    ) or frappe.db.get_value("Stock Entry Type", {"purpose": "Material Issue"}, "name")
    if not value:
        frappe.throw("ERPNext setup requires a Material Issue Stock Entry Type")
    return value


def validate_partner_return_availability(document: Any) -> Decimal:
    """Lock the source lot and reject reserved or already-issued quantities.

    This runs before the parent document is marked submitted, then is repeated
    while posting. The row lock is held by the request transaction across both
    checks, closing the race with FIFO reservation and checkout submission.
    """
    import frappe

    locked = frappe.db.sql(
        "select name, reserved_qty from `tabCC Stock Lot` where name = %s for update",
        (document.source_lot,),
        as_dict=True,
    )
    if not locked:
        frappe.throw(f"CC Stock Lot {document.source_lot} does not exist")
    balance = get_ownership_balance(document.source_lot)
    reserved = Decimal(str(locked[0].reserved_qty or 0))
    qty = Decimal(str(document.qty))
    available = balance - reserved
    if qty > available:
        frappe.throw(f"Partner return quantity {qty} exceeds unreserved balance {available}")
    return available


@contextmanager
def _cancellation(frappe: Any):
    previous = getattr(frappe.flags, CANCELLATION_FLAG, False)
    setattr(frappe.flags, CANCELLATION_FLAG, True)
    try:
        yield
    finally:
        setattr(frappe.flags, CANCELLATION_FLAG, previous)


def post_partner_return(document: Any) -> str:
    import frappe

    existing = frappe.db.get_value(
        "Stock Entry",
        {PARTNER_RETURN_FIELD: document.name, "docstatus": ("!=", 2)},
        "name",
    )
    if existing:
        if frappe.db.get_value("Stock Entry", existing, "docstatus") != 1:
            frappe.throw(f"Existing Stock Entry {existing} is not submitted")
        document.db_set("stock_entry", existing, update_modified=False)
        return existing
    validate_partner_return_availability(document)
    company = frappe.get_cached_value(
        "Company",
        document.company,
        ["stock_adjustment_account", "default_expense_account", "cost_center"],
        as_dict=True,
    )
    expense_account = company.stock_adjustment_account or company.default_expense_account
    if not expense_account or not company.cost_center:
        frappe.throw("Partner return Company requires Stock Adjustment/Expense and Cost Center")
    stock_entry = frappe.get_doc(
        {
            "doctype": "Stock Entry",
            "purpose": "Material Issue",
            "stock_entry_type": _material_issue_type(frappe),
            "company": document.company,
            "posting_date": document.posting_date,
            "posting_time": document.posting_time,
            "set_posting_time": 1,
            PARTNER_RETURN_FIELD: document.name,
        }
    )
    row = stock_entry.append(
        "items",
        {
            "item_code": document.item_code,
            "qty": document.qty,
            "uom": document.stock_uom,
            "stock_uom": document.stock_uom,
            "conversion_factor": 1,
            "s_warehouse": document.warehouse,
            "basic_rate": 0,
            "allow_zero_valuation_rate": 1,
            "set_basic_rate_manually": 1,
            "expense_account": expense_account,
            "cost_center": company.cost_center,
            "use_serial_batch_fields": int(document.tracking_type != "NONE"),
            "batch_no": document.batch_no,
            "serial_no": document.serial_numbers,
        },
    )
    row.set(OWNERSHIP_FIELD, document.source_lot)
    stock_entry.insert(ignore_permissions=True)
    stock_entry.submit()
    document.db_set("stock_entry", stock_entry.name, update_modified=False)
    document.stock_entry = stock_entry.name
    document.db_set("status", "RETURNED", update_modified=False)
    document.status = "RETURNED"
    remaining = get_ownership_balance(document.source_lot)
    if remaining == 0:
        frappe.db.set_value(
            "CC Stock Lot",
            document.source_lot,
            "lot_status",
            "EXHAUSTED",
            update_modified=False,
        )
    return stock_entry.name


def cancel_partner_return(document: Any) -> None:
    import frappe

    if not document.stock_entry:
        frappe.throw("Submitted CC Partner Return has no linked Stock Entry")
    stock_entry = frappe.get_doc("Stock Entry", document.stock_entry)
    if stock_entry.get(PARTNER_RETURN_FIELD) != document.name:
        frappe.throw("Linked Stock Entry belongs to another CC Partner Return")
    if stock_entry.docstatus == 1:
        ignored = set(stock_entry.get("ignore_linked_doctypes") or ())
        ignored.update(BACKLINK_DOCTYPES)
        stock_entry.ignore_linked_doctypes = tuple(sorted(ignored))
        with _cancellation(frappe):
            stock_entry.cancel()
    elif stock_entry.docstatus != 2:
        frappe.throw("Linked partner-return Stock Entry has an invalid state")
    frappe.db.set_value(
        "CC Stock Lot",
        document.source_lot,
        "lot_status",
        document.previous_lot_status or "OPEN",
        update_modified=False,
    )


def guard_partner_return_stock_entry(doc: Any) -> None:
    import frappe

    if doc.get(PARTNER_RETURN_FIELD) and not getattr(
        frappe.flags,
        CANCELLATION_FLAG,
        False,
    ):
        frappe.throw(
            f"Cancel linked CC Partner Return {doc.get(PARTNER_RETURN_FIELD)} "
            "instead of its Stock Entry"
        )
