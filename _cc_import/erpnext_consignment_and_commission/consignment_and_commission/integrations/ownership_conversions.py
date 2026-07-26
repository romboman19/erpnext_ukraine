"""Audited third-party stock purchase into standard ERPNext OWN inventory."""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from typing import Any

from ..services.ownership_conversion import (
    OwnershipConversionError,
    OwnershipConversionRequest,
    ownership_conversion_fingerprint,
    validate_ownership_conversion_request,
)
from ..services.partner_return import canonical_posting_time
from ..services.stock_lot import get_ownership_balance
from ..setup.ownership_dimension import OWNERSHIP_CONVERSION_FIELD, OWNERSHIP_FIELD

CANCELLATION_FLAG = "cc_ownership_conversion_cancellation"
BACKLINK_DOCTYPES = {"CC Ownership Conversion"}


def _serials(value: str | None) -> tuple[str, ...]:
    return tuple(line.strip() for line in (value or "").splitlines() if line.strip())


@contextmanager
def _cancellation(frappe: Any):
    previous = getattr(frappe.flags, CANCELLATION_FLAG, False)
    setattr(frappe.flags, CANCELLATION_FLAG, True)
    try:
        yield
    finally:
        setattr(frappe.flags, CANCELLATION_FLAG, previous)


def _existing_conversion(
    frappe: Any,
    request: OwnershipConversionRequest,
    fingerprint: str,
) -> Any | None:
    name = frappe.db.get_value(
        "CC Ownership Conversion",
        {"idempotency_key": request.idempotency_key},
        "name",
    )
    if not name:
        return None
    document = frappe.get_doc("CC Ownership Conversion", name)
    if document.request_fingerprint != fingerprint:
        raise OwnershipConversionError(
            f"Ownership conversion idempotency key {request.idempotency_key!r} "
            "belongs to another request"
        )
    return document


def create_ownership_conversion(request: OwnershipConversionRequest) -> Any:
    """Create or replay one draft conversion command."""
    import frappe

    validate_ownership_conversion_request(request)
    fingerprint = ownership_conversion_fingerprint(request)
    existing = _existing_conversion(frappe, request, fingerprint)
    if existing:
        return existing
    document = frappe.get_doc(
        {
            "doctype": "CC Ownership Conversion",
            "idempotency_key": request.idempotency_key,
            "request_fingerprint": fingerprint,
            "posting_date": request.posting_date,
            "posting_time": canonical_posting_time(request.posting_time),
            "source_lot": request.source_lot,
            "qty": request.qty,
            "source_method": request.source_method,
            "unit_cost": request.unit_cost,
            "currency": request.currency,
            "exchange_rate": request.exchange_rate,
            "reason": request.reason,
            "due_date": request.due_date,
            "supplier_invoice_no": request.supplier_invoice_no,
            "supplier_invoice_date": request.supplier_invoice_date,
            "serial_numbers": "\n".join(request.serial_numbers),
            "target_batch_no": request.target_batch_no,
        }
    )
    savepoint = "cc_ownership_conversion_create"
    frappe.db.savepoint(savepoint)
    try:
        document.insert(ignore_permissions=True)
    except frappe.DuplicateEntryError:
        frappe.db.rollback(save_point=savepoint)
        existing = _existing_conversion(frappe, request, fingerprint)
        if existing:
            return existing
        raise
    return document


def validate_conversion_availability(document: Any) -> Decimal:
    """Lock the source lot before submit and exclude all active reservations."""
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
    available = balance - reserved
    qty = Decimal(str(document.qty))
    if qty > available:
        frappe.throw(f"Conversion quantity {qty} exceeds unreserved balance {available}")
    return available


def _material_issue_type(frappe: Any) -> str:
    value = frappe.db.get_value(
        "Stock Entry Type",
        {"purpose": "Material Issue", "is_standard": 1},
        "name",
    ) or frappe.db.get_value("Stock Entry Type", {"purpose": "Material Issue"}, "name")
    if not value:
        frappe.throw("ERPNext setup requires a Material Issue Stock Entry Type")
    return value


def _create_source_issue(frappe: Any, document: Any) -> Any:
    existing = frappe.db.get_value(
        "Stock Entry",
        {OWNERSHIP_CONVERSION_FIELD: document.name, "docstatus": ("!=", 2)},
        "name",
    )
    if existing:
        issue = frappe.get_doc("Stock Entry", existing)
        if issue.docstatus != 1:
            frappe.throw(f"Existing conversion Stock Entry {existing} is not submitted")
        return issue
    company = frappe.get_cached_value(
        "Company",
        document.company,
        ["stock_adjustment_account", "default_expense_account", "cost_center"],
        as_dict=True,
    )
    expense_account = company.stock_adjustment_account or company.default_expense_account
    if not expense_account or not company.cost_center:
        frappe.throw("Conversion Company requires Stock Adjustment/Expense and Cost Center")
    issue = frappe.get_doc(
        {
            "doctype": "Stock Entry",
            "purpose": "Material Issue",
            "stock_entry_type": _material_issue_type(frappe),
            "company": document.company,
            "posting_date": document.posting_date,
            "posting_time": document.posting_time,
            "set_posting_time": 1,
            OWNERSHIP_CONVERSION_FIELD: document.name,
        }
    )
    row = issue.append(
        "items",
        {
            "item_code": document.item_code,
            "qty": document.qty,
            "uom": document.stock_uom,
            "stock_uom": document.stock_uom,
            "conversion_factor": 1,
            "s_warehouse": document.source_warehouse,
            "basic_rate": 0,
            "allow_zero_valuation_rate": 1,
            "set_basic_rate_manually": 1,
            "expense_account": expense_account,
            "cost_center": company.cost_center,
            "use_serial_batch_fields": int(document.tracking_type != "NONE"),
            "batch_no": document.source_batch_no,
            "serial_no": document.serial_numbers,
        },
    )
    row.set(OWNERSHIP_FIELD, document.source_lot)
    issue.insert(ignore_permissions=True)
    issue.submit()
    return issue


def _ensure_target_batch(frappe: Any, document: Any) -> None:
    if document.tracking_type != "BATCH":
        return
    if frappe.db.exists("Batch", document.target_batch_no):
        batch = frappe.db.get_value(
            "Batch",
            document.target_batch_no,
            ["item", OWNERSHIP_FIELD],
            as_dict=True,
        )
        if batch.item != document.item_code or batch.get(OWNERSHIP_FIELD):
            frappe.throw(f"Target Batch {document.target_batch_no} is already in use")
        return
    frappe.get_doc(
        {
            "doctype": "Batch",
            "batch_id": document.target_batch_no,
            "item": document.item_code,
        }
    ).insert(ignore_permissions=True)


def _create_own_receipt(frappe: Any, document: Any) -> Any:
    existing = frappe.db.get_value(
        "CC Own Receipt",
        {"ownership_conversion": document.name, "docstatus": ("!=", 2)},
        "name",
    )
    if existing:
        receipt = frappe.get_doc("CC Own Receipt", existing)
        if receipt.docstatus != 1:
            frappe.throw(f"Existing conversion CC Own Receipt {existing} is not submitted")
        return receipt
    receipt = frappe.get_doc(
        {
            "doctype": "CC Own Receipt",
            "ownership_conversion": document.name,
            "source_method": document.source_method,
            "posting_date": document.posting_date,
            "posting_time": document.posting_time,
            "due_date": document.due_date,
            "supplier": document.supplier,
            "company": document.company,
            "location": document.location,
            "currency": document.currency,
            "conversion_rate": document.exchange_rate,
            "supplier_invoice_no": document.supplier_invoice_no,
            "supplier_invoice_date": document.supplier_invoice_date,
            "notes": f"Ownership conversion {document.name}: {document.reason}",
            "items": [
                {
                    "item_code": document.item_code,
                    "qty": document.qty,
                    "uom": document.stock_uom,
                    "conversion_factor": 1,
                    "rate": document.unit_cost,
                    "batch_no": document.target_batch_no,
                    "serial_numbers": document.serial_numbers,
                }
            ],
        }
    ).insert(ignore_permissions=True)
    receipt.submit()
    receipt.reload()
    return receipt


def conversion_allows_existing_serial(receipt: Any, serial_no: str) -> bool:
    """Allow only the exact just-issued Serial Nos into the generated OWN receipt."""
    import frappe

    if not receipt.get("ownership_conversion"):
        return False
    conversion = frappe.get_doc("CC Ownership Conversion", receipt.ownership_conversion)
    if conversion.docstatus != 1 or serial_no not in _serials(conversion.serial_numbers):
        return False
    serial = frappe.db.get_value(
        "Serial No",
        serial_no,
        ["item_code", "warehouse", OWNERSHIP_FIELD],
        as_dict=True,
    )
    return bool(
        serial
        and serial.item_code == conversion.item_code
        and not serial.warehouse
        and serial.get(OWNERSHIP_FIELD) == conversion.source_lot
    )


def validate_conversion_own_receipt(receipt: Any) -> None:
    """Fail closed if a generated own receipt differs from its submitted command."""
    import frappe

    if not receipt.get("ownership_conversion"):
        return
    conversion = frappe.get_doc("CC Ownership Conversion", receipt.ownership_conversion)
    if conversion.docstatus != 1:
        frappe.throw("Conversion-linked CC Own Receipt requires a submitted conversion")
    existing = frappe.db.get_value(
        "CC Own Receipt",
        {
            "ownership_conversion": conversion.name,
            "name": ("!=", receipt.name or ""),
            "docstatus": ("!=", 2),
        },
        "name",
    )
    if existing:
        frappe.throw(f"CC Ownership Conversion {conversion.name} already has {existing}")
    expected = {
        "source_method": conversion.source_method,
        "company": conversion.company,
        "location": conversion.location,
        "supplier": conversion.supplier,
        "currency": conversion.currency,
    }
    mismatches = [
        fieldname
        for fieldname, value in expected.items()
        if str(receipt.get(fieldname) or "") != str(value or "")
    ]
    if Decimal(str(receipt.conversion_rate)) != Decimal(str(conversion.exchange_rate)):
        mismatches.append("conversion_rate")
    if canonical_posting_time(receipt.posting_time) != canonical_posting_time(
        conversion.posting_time
    ) or str(receipt.posting_date) != str(conversion.posting_date):
        mismatches.append("posting_datetime")
    if len(receipt.items) != 1:
        mismatches.append("items")
    else:
        row = receipt.items[0]
        if (
            row.item_code != conversion.item_code
            or Decimal(str(row.stock_qty)) != Decimal(str(conversion.qty))
            or Decimal(str(row.rate)) != Decimal(str(conversion.unit_cost))
            or (row.batch_no or "") != (conversion.target_batch_no or "")
            or set(_serials(row.serial_numbers)) != set(_serials(conversion.serial_numbers))
        ):
            mismatches.append("items")
    if mismatches:
        frappe.throw(f"Conversion-linked CC Own Receipt mismatch: {', '.join(mismatches)}")


def transition_conversion_serial_ownership(
    frappe: Any,
    *,
    receipt: Any,
    target_lot: str,
) -> tuple[str, ...]:
    if not receipt.get("ownership_conversion"):
        return ()
    conversion = frappe.get_doc("CC Ownership Conversion", receipt.ownership_conversion)
    if conversion.tracking_type != "SERIAL":
        return ()
    serials = _serials(conversion.serial_numbers)
    for serial_no in serials:
        if not conversion_allows_existing_serial(receipt, serial_no):
            frappe.throw(f"Serial No {serial_no} is not ready for ownership conversion")
        frappe.db.set_value(
            "Serial No",
            serial_no,
            OWNERSHIP_FIELD,
            target_lot,
            update_modified=False,
        )
    return serials


def restore_transitioned_serials(
    frappe: Any,
    *,
    conversion: Any,
    expected_owner: str,
) -> None:
    if conversion.tracking_type != "SERIAL":
        return
    for serial_no in _serials(conversion.serial_numbers):
        serial = frappe.db.get_value(
            "Serial No",
            serial_no,
            ["warehouse", OWNERSHIP_FIELD],
            as_dict=True,
        )
        if not serial or serial.warehouse or serial.get(OWNERSHIP_FIELD) != expected_owner:
            frappe.throw(f"Serial No {serial_no} cannot be restored to its source conversion lot")
        frappe.db.set_value(
            "Serial No",
            serial_no,
            OWNERSHIP_FIELD,
            conversion.source_lot,
            update_modified=False,
        )


def post_ownership_conversion(document: Any) -> str:
    import frappe

    validate_conversion_availability(document)
    issue = _create_source_issue(frappe, document)
    _ensure_target_batch(frappe, document)
    receipt = _create_own_receipt(frappe, document)
    if len(receipt.items) != 1 or not receipt.items[0].stock_lot:
        frappe.throw("Conversion CC Own Receipt did not create one target CC Stock Lot")
    target_lot = receipt.items[0].stock_lot
    values = {
        "source_issue": issue.name,
        "own_receipt": receipt.name,
        "target_lot": target_lot,
        "purchase_invoice": receipt.purchase_invoice,
        "status": "CONVERTED",
    }
    frappe.db.set_value(
        "CC Ownership Conversion",
        document.name,
        values,
        update_modified=False,
    )
    for fieldname, value in values.items():
        document.set(fieldname, value)
    if get_ownership_balance(document.source_lot) == 0:
        frappe.db.set_value(
            "CC Stock Lot",
            document.source_lot,
            "lot_status",
            "EXHAUSTED",
            update_modified=False,
        )
    return receipt.purchase_invoice


def cancel_ownership_conversion(document: Any) -> None:
    import frappe

    if not all((document.source_issue, document.own_receipt, document.target_lot)):
        frappe.throw("Submitted CC Ownership Conversion has incomplete linked evidence")
    receipt = frappe.get_doc("CC Own Receipt", document.own_receipt)
    if receipt.get("ownership_conversion") != document.name:
        frappe.throw("Linked CC Own Receipt belongs to another conversion")
    if receipt.docstatus == 1:
        ignored = set(receipt.get("ignore_linked_doctypes") or ())
        ignored.update(BACKLINK_DOCTYPES)
        receipt.ignore_linked_doctypes = tuple(sorted(ignored))
        with _cancellation(frappe):
            receipt.cancel()
    elif receipt.docstatus != 2:
        frappe.throw("Linked conversion CC Own Receipt has an invalid state")
    restore_transitioned_serials(
        frappe,
        conversion=document,
        expected_owner=document.target_lot,
    )
    issue = frappe.get_doc("Stock Entry", document.source_issue)
    if issue.get(OWNERSHIP_CONVERSION_FIELD) != document.name:
        frappe.throw("Linked Stock Entry belongs to another conversion")
    if issue.docstatus == 1:
        ignored = set(issue.get("ignore_linked_doctypes") or ())
        ignored.update(BACKLINK_DOCTYPES)
        issue.ignore_linked_doctypes = tuple(sorted(ignored))
        with _cancellation(frappe):
            issue.cancel()
    elif issue.docstatus != 2:
        frappe.throw("Linked conversion Stock Entry has an invalid state")
    frappe.db.set_value(
        "CC Stock Lot",
        document.source_lot,
        "lot_status",
        document.previous_lot_status or "OPEN",
        update_modified=False,
    )


def guard_conversion_stock_entry(document: Any) -> None:
    import frappe

    if document.get(OWNERSHIP_CONVERSION_FIELD) and not getattr(
        frappe.flags,
        CANCELLATION_FLAG,
        False,
    ):
        frappe.throw(
            f"Cancel linked CC Ownership Conversion "
            f"{document.get(OWNERSHIP_CONVERSION_FIELD)} instead of its Stock Entry"
        )


def guard_conversion_own_receipt(document: Any) -> None:
    import frappe

    if document.get("ownership_conversion") and not getattr(
        frappe.flags,
        CANCELLATION_FLAG,
        False,
    ):
        frappe.throw(
            f"Cancel linked CC Ownership Conversion {document.ownership_conversion} "
            "instead of its CC Own Receipt"
        )
