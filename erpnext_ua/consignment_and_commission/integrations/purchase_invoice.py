"""Controlled Purchase Invoice lifecycle for company-owned CC stock."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from ..setup.ownership_dimension import (
    OWN_RECEIPT_FIELD,
    OWN_RECEIPT_ITEM_FIELD,
    OWNERSHIP_CONVERSION_FIELD,
    OWNERSHIP_FIELD,
)
from .tracking import assign_receipt_tracking_ownership

CANCELLATION_FLAG = "cc_own_receipt_cancellation"
OWN_RECEIPT_BACKLINK_DOCTYPES = {
    "CC Own Receipt",
    "CC Ownership Conversion",
    "CC Stock Lot",
}


def _assert_schema(frappe: Any) -> None:
    required = (
        ("Purchase Invoice", OWN_RECEIPT_FIELD),
        ("Purchase Invoice", OWNERSHIP_CONVERSION_FIELD),
        ("Purchase Invoice Item", OWN_RECEIPT_ITEM_FIELD),
        ("Purchase Invoice Item", OWNERSHIP_FIELD),
        ("Stock Ledger Entry", OWNERSHIP_FIELD),
    )
    missing = [
        f"{doctype}.{fieldname}"
        for doctype, fieldname in required
        if not frappe.db.has_column(doctype, fieldname)
    ]
    if missing:
        frappe.throw(f"CC OWN purchase schema is incomplete; run bench migrate: {', '.join(missing)}")


def _existing_purchase_invoice(frappe: Any, receipt_name: str) -> str | None:
    return frappe.db.get_value(
        "Purchase Invoice",
        {OWN_RECEIPT_FIELD: receipt_name, "docstatus": ("!=", 2)},
        "name",
    )


def _ensure_lot(frappe: Any, receipt: Any, line: Any) -> Any:
    existing = frappe.db.get_value("CC Stock Lot", {"own_receipt_item_row": line.name}, "name")
    if existing:
        return frappe.get_doc("CC Stock Lot", existing)

    from frappe.utils import get_datetime

    return frappe.get_doc(
        {
            "doctype": "CC Stock Lot",
            "lot_status": "PENDING",
            "source_method": receipt.source_method,
            "own_receipt": receipt.name,
            "own_receipt_item_row": line.name,
            "company": receipt.company,
            "location": receipt.location,
            "supplier": receipt.supplier,
            "relationship_model": "OWN",
            "ownership_conversion": receipt.ownership_conversion,
            "item_code": line.item_code,
            "stock_uom": line.stock_uom,
            "tracking_type": line.tracking_type,
            "batch_no": line.batch_no,
            "serial_numbers": line.serial_numbers,
            "received_qty": line.stock_qty,
            "reserved_qty": 0,
            "received_datetime": get_datetime(f"{receipt.posting_date} {receipt.posting_time}"),
            "warehouse": receipt.warehouse,
        }
    ).insert(ignore_permissions=True)


def post_own_receipt_purchase(receipt: Any) -> str:
    import frappe
    from erpnext.accounts.party import get_party_account

    _assert_schema(frappe)
    existing = _existing_purchase_invoice(frappe, receipt.name)
    if existing:
        if frappe.db.get_value("Purchase Invoice", existing, "docstatus") != 1:
            frappe.throw(f"Existing Purchase Invoice {existing} for CC Own Receipt is not submitted")
        receipt.db_set("purchase_invoice", existing, update_modified=False)
        return existing

    company = frappe.get_cached_value(
        "Company",
        receipt.company,
        ["default_expense_account", "cost_center"],
        as_dict=True,
    )
    if not company.default_expense_account or not company.cost_center:
        frappe.throw("Receipt Company requires a Default Expense Account and Cost Center")
    payable_account = get_party_account("Supplier", receipt.supplier, receipt.company)
    if not payable_account:
        frappe.throw("Receipt Supplier requires a payable account for the selected Company")

    lots = [_ensure_lot(frappe, receipt, line) for line in receipt.items]
    purchase_invoice = frappe.get_doc(
        {
            "doctype": "Purchase Invoice",
            "company": receipt.company,
            "supplier": receipt.supplier,
            "posting_date": receipt.posting_date,
            "posting_time": receipt.posting_time,
            "set_posting_time": 1,
            "due_date": receipt.due_date,
            "bill_no": receipt.supplier_invoice_no or receipt.name,
            "bill_date": receipt.supplier_invoice_date or receipt.posting_date,
            "update_stock": 1,
            "currency": receipt.currency,
            "conversion_rate": receipt.conversion_rate,
            "credit_to": payable_account,
            OWN_RECEIPT_FIELD: receipt.name,
            OWNERSHIP_CONVERSION_FIELD: receipt.ownership_conversion,
        }
    )
    for line, lot in zip(receipt.items, lots, strict=True):
        row = purchase_invoice.append(
            "items",
            {
                "item_code": line.item_code,
                "item_name": line.item_name,
                "description": line.description or line.item_name,
                "warehouse": receipt.warehouse,
                "qty": line.stock_qty,
                "uom": line.stock_uom,
                "stock_uom": line.stock_uom,
                "conversion_factor": 1,
                "rate": line.rate,
                "price_list_rate": line.rate,
                "expense_account": company.default_expense_account,
                "cost_center": company.cost_center,
                "use_serial_batch_fields": int(line.tracking_type != "NONE"),
                "batch_no": line.batch_no,
                "serial_no": line.serial_numbers,
                OWN_RECEIPT_ITEM_FIELD: line.name,
            },
        )
        row.set(OWNERSHIP_FIELD, lot.name)

    transitioned_serials: tuple[str, ...] = ()
    if receipt.ownership_conversion:
        from .ownership_conversions import transition_conversion_serial_ownership

        if len(lots) != 1:
            frappe.throw("Ownership conversion must create exactly one OWN CC Stock Lot")
        transitioned_serials = transition_conversion_serial_ownership(
            frappe,
            receipt=receipt,
            target_lot=lots[0].name,
        )
    try:
        purchase_invoice.insert(ignore_permissions=True)
        purchase_invoice.submit()
    except Exception:
        if transitioned_serials and receipt.ownership_conversion:
            conversion = frappe.get_doc("CC Ownership Conversion", receipt.ownership_conversion)
            for serial_no in transitioned_serials:
                frappe.db.set_value(
                    "Serial No",
                    serial_no,
                    OWNERSHIP_FIELD,
                    conversion.source_lot,
                    update_modified=False,
                )
        raise
    purchase_invoice.reload()
    receipt.db_set("purchase_invoice", purchase_invoice.name, update_modified=False)
    receipt.purchase_invoice = purchase_invoice.name

    detail_by_receipt_row = {
        row.get(OWN_RECEIPT_ITEM_FIELD): row for row in purchase_invoice.items
    }
    for line, lot in zip(receipt.items, lots, strict=True):
        detail = detail_by_receipt_row[line.name]
        tracking_values = assign_receipt_tracking_ownership(
            frappe,
            stock_entry_row=detail,
            stock_lot=lot.name,
            tracking_type=line.tracking_type,
        )
        frappe.db.set_value(
            "CC Own Receipt Item",
            line.name,
            {
                "stock_lot": lot.name,
                "purchase_invoice_item": detail.name,
                **tracking_values,
            },
            update_modified=False,
        )
        line.stock_lot = lot.name
        line.purchase_invoice_item = detail.name
        line.batch_no = tracking_values["batch_no"]
        line.serial_numbers = tracking_values["serial_numbers"]
        frappe.db.set_value(
            "CC Stock Lot",
            lot.name,
            {
                "lot_status": "OPEN",
                "purchase_invoice": purchase_invoice.name,
                "purchase_invoice_item": detail.name,
                **tracking_values,
            },
            update_modified=False,
        )
    return purchase_invoice.name


@contextmanager
def _own_receipt_cancellation(frappe: Any):
    previous = getattr(frappe.flags, CANCELLATION_FLAG, False)
    setattr(frappe.flags, CANCELLATION_FLAG, True)
    try:
        yield
    finally:
        setattr(frappe.flags, CANCELLATION_FLAG, previous)


def cancel_own_receipt_purchase(receipt: Any) -> None:
    import frappe

    if not receipt.purchase_invoice:
        frappe.throw("Submitted CC Own Receipt has no linked Purchase Invoice")
    invoice = frappe.get_doc("Purchase Invoice", receipt.purchase_invoice)
    if invoice.get(OWN_RECEIPT_FIELD) != receipt.name:
        frappe.throw("Linked Purchase Invoice belongs to another CC Own Receipt")
    if invoice.docstatus == 1:
        with _own_receipt_cancellation(frappe):
            invoice.cancel()
    elif invoice.docstatus != 2:
        frappe.throw("Linked Purchase Invoice must be submitted before CC Own Receipt cancellation")

    for lot_name in frappe.get_all(
        "CC Stock Lot",
        filters={"own_receipt": receipt.name},
        pluck="name",
    ):
        frappe.db.set_value(
            "CC Stock Lot",
            lot_name,
            {"lot_status": "CANCELLED", "reserved_qty": 0},
            update_modified=False,
        )


def guard_linked_own_receipt_cancellation(doc: Any, method: str | None = None) -> None:
    del method
    import frappe

    if doc.get(OWN_RECEIPT_FIELD) and not getattr(frappe.flags, CANCELLATION_FLAG, False):
        frappe.throw(
            f"Cancel linked CC Own Receipt {doc.get(OWN_RECEIPT_FIELD)} instead of this Purchase Invoice"
        )


def allow_linked_own_receipt_cancellation(doc: Any, method: str | None = None) -> None:
    del method
    import frappe

    if not doc.get(OWN_RECEIPT_FIELD) or not getattr(frappe.flags, CANCELLATION_FLAG, False):
        return
    ignored_doctypes = set(doc.get("ignore_linked_doctypes") or ())
    ignored_doctypes.update(OWN_RECEIPT_BACKLINK_DOCTYPES)
    doc.ignore_linked_doctypes = tuple(sorted(ignored_doctypes))
