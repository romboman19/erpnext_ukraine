"""Controlled zero-value Stock Entry lifecycle for CC Receipt."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from ..setup.ownership_dimension import (
    OWNERSHIP_CONVERSION_FIELD,
    OWNERSHIP_FIELD,
    PARTNER_RETURN_FIELD,
    RECEIPT_FIELD,
    RECEIPT_ITEM_FIELD,
)
from .tracking import assign_receipt_tracking_ownership

CANCELLATION_FLAG = "cc_receipt_cancellation"
RECEIPT_BACKLINK_DOCTYPES = {"CC Receipt", "CC Stock Lot"}


def _assert_schema(frappe: Any) -> None:
    required = (
        ("Stock Entry", RECEIPT_FIELD),
        ("Stock Entry Detail", RECEIPT_ITEM_FIELD),
        ("Stock Entry Detail", f"to_{OWNERSHIP_FIELD}"),
        ("Stock Ledger Entry", OWNERSHIP_FIELD),
    )
    missing = [
        f"{doctype}.{fieldname}"
        for doctype, fieldname in required
        if not frappe.db.has_column(doctype, fieldname)
    ]
    if missing:
        frappe.throw(f"CC ownership schema is incomplete; run bench migrate: {', '.join(missing)}")


def _existing_stock_entry(frappe: Any, receipt_name: str) -> str | None:
    return frappe.db.get_value(
        "Stock Entry",
        {RECEIPT_FIELD: receipt_name, "docstatus": ("!=", 2)},
        "name",
    )


def _material_receipt_type(frappe: Any) -> str:
    stock_entry_type = frappe.db.get_value(
        "Stock Entry Type",
        {"purpose": "Material Receipt", "is_standard": 1},
        "name",
    ) or frappe.db.get_value("Stock Entry Type", {"purpose": "Material Receipt"}, "name")
    if not stock_entry_type:
        frappe.throw("ERPNext setup requires a Material Receipt Stock Entry Type")
    return stock_entry_type


def _ensure_lot(frappe: Any, receipt: Any, line: Any) -> Any:
    existing = frappe.db.get_value("CC Stock Lot", {"receipt_item_row": line.name}, "name")
    if existing:
        return frappe.get_doc("CC Stock Lot", existing)

    from frappe.utils import get_datetime

    from .sale_allocations import get_account_mapping

    mapping = get_account_mapping(frappe, receipt.company)
    company_currency = frappe.get_cached_value("Company", receipt.company, "default_currency")
    if not company_currency:
        frappe.throw(f"Receipt Company {receipt.company} requires a default currency")

    return frappe.get_doc(
        {
            "doctype": "CC Stock Lot",
            "lot_status": "PENDING",
            "receipt": receipt.name,
            "receipt_item_row": line.name,
            "partner_profile": receipt.partner_profile,
            "contract": receipt.contract,
            "company": receipt.company,
            "location": receipt.location,
            "supplier": receipt.supplier,
            "relationship_model": receipt.relationship_model,
            "source_method": receipt.relationship_model,
            "item_code": line.item_code,
            "stock_uom": line.stock_uom,
            "tracking_type": line.tracking_type,
            "batch_no": line.batch_no,
            "serial_numbers": line.serial_numbers,
            "received_qty": line.stock_qty,
            "reserved_qty": 0,
            "received_datetime": get_datetime(f"{receipt.posting_date} {receipt.posting_time}"),
            "warehouse": receipt.warehouse,
            "off_balance_account": mapping.off_balance_goods_account,
            "off_balance_unit_value": line.accounting_unit_value,
            "off_balance_amount": line.accounting_amount,
            "off_balance_currency": company_currency,
        }
    ).insert(ignore_permissions=True)


def post_receipt_stock(receipt: Any) -> str:
    import frappe

    _assert_schema(frappe)
    existing = _existing_stock_entry(frappe, receipt.name)
    if existing:
        if frappe.db.get_value("Stock Entry", existing, "docstatus") != 1:
            frappe.throw(f"Existing Stock Entry {existing} for CC Receipt is not submitted")
        receipt.db_set("stock_entry", existing, update_modified=False)
        return existing

    company = frappe.get_cached_value(
        "Company",
        receipt.company,
        ["stock_adjustment_account", "default_expense_account", "cost_center"],
        as_dict=True,
    )
    expense_account = company.stock_adjustment_account or company.default_expense_account
    if not expense_account or not company.cost_center:
        frappe.throw("Receipt Company requires Stock Adjustment/Expense Account and Cost Center")

    lots = [_ensure_lot(frappe, receipt, line) for line in receipt.items]
    stock_entry = frappe.get_doc(
        {
            "doctype": "Stock Entry",
            "purpose": "Material Receipt",
            "stock_entry_type": _material_receipt_type(frappe),
            "company": receipt.company,
            "posting_date": receipt.posting_date,
            "posting_time": receipt.posting_time,
            "set_posting_time": 1,
            RECEIPT_FIELD: receipt.name,
        }
    )
    for line, lot in zip(receipt.items, lots, strict=True):
        row = stock_entry.append(
            "items",
            {
                "item_code": line.item_code,
                "item_name": line.item_name,
                "description": line.description or line.item_name,
                "qty": line.stock_qty,
                "uom": line.stock_uom,
                "stock_uom": line.stock_uom,
                "conversion_factor": 1,
                "t_warehouse": receipt.warehouse,
                "basic_rate": 0,
                "allow_zero_valuation_rate": 1,
                "set_basic_rate_manually": 1,
                "expense_account": expense_account,
                "cost_center": company.cost_center,
                "use_serial_batch_fields": int(line.tracking_type != "NONE"),
                "batch_no": line.batch_no,
                "serial_no": line.serial_numbers,
                RECEIPT_ITEM_FIELD: line.name,
            },
        )
        row.set(f"to_{OWNERSHIP_FIELD}", lot.name)

    stock_entry.insert(ignore_permissions=True)
    stock_entry.submit()
    stock_entry.reload()
    receipt.db_set("stock_entry", stock_entry.name, update_modified=False)
    receipt.stock_entry = stock_entry.name

    detail_by_receipt_row = {row.get(RECEIPT_ITEM_FIELD): row for row in stock_entry.items}
    for line, lot in zip(receipt.items, lots, strict=True):
        detail = detail_by_receipt_row[line.name]
        tracking_values = assign_receipt_tracking_ownership(
            frappe,
            stock_entry_row=detail,
            stock_lot=lot.name,
            tracking_type=line.tracking_type,
        )
        frappe.db.set_value(
            "CC Receipt Item",
            line.name,
            {
                "stock_lot": lot.name,
                "stock_entry_detail": detail.name,
                **tracking_values,
            },
            update_modified=False,
        )
        line.stock_lot = lot.name
        line.stock_entry_detail = detail.name
        line.batch_no = tracking_values["batch_no"]
        line.serial_numbers = tracking_values["serial_numbers"]
        frappe.db.set_value(
            "CC Stock Lot",
            lot.name,
            {
                "lot_status": "OPEN",
                "stock_entry": stock_entry.name,
                "stock_entry_detail": detail.name,
                **tracking_values,
            },
            update_modified=False,
        )
    return stock_entry.name


@contextmanager
def _receipt_cancellation(frappe: Any):
    previous = getattr(frappe.flags, CANCELLATION_FLAG, False)
    setattr(frappe.flags, CANCELLATION_FLAG, True)
    try:
        yield
    finally:
        setattr(frappe.flags, CANCELLATION_FLAG, previous)


def cancel_receipt_stock(receipt: Any) -> None:
    import frappe

    lot_names = frappe.get_all(
        "CC Stock Lot",
        filters={"receipt": receipt.name},
        pluck="name",
    )
    if lot_names and frappe.db.exists(
        "CC Partner Return",
        {"source_lot": ("in", lot_names), "docstatus": 1},
    ):
        frappe.throw("Cancel submitted CC Partner Returns before cancelling this receipt")
    if lot_names and frappe.db.exists(
        "CC Ownership Conversion",
        {"source_lot": ("in", lot_names), "docstatus": 1},
    ):
        frappe.throw("Cancel submitted CC Ownership Conversions before cancelling this receipt")
    if not receipt.stock_entry:
        frappe.throw("Submitted CC Receipt has no linked Stock Entry")
    stock_entry = frappe.get_doc("Stock Entry", receipt.stock_entry)
    if stock_entry.get(RECEIPT_FIELD) != receipt.name:
        frappe.throw("Linked Stock Entry belongs to another CC Receipt")
    if stock_entry.docstatus == 1:
        with _receipt_cancellation(frappe):
            stock_entry.cancel()
    elif stock_entry.docstatus != 2:
        frappe.throw("Linked Stock Entry must be submitted before CC Receipt cancellation")

    for lot_name in lot_names:
        frappe.db.set_value(
            "CC Stock Lot",
            lot_name,
            {"lot_status": "CANCELLED", "reserved_qty": 0},
            update_modified=False,
        )


def guard_linked_receipt_cancellation(doc: Any, method: str | None = None) -> None:
    del method
    import frappe

    if doc.get(RECEIPT_FIELD) and not getattr(frappe.flags, CANCELLATION_FLAG, False):
        frappe.throw(f"Cancel linked CC Receipt {doc.get(RECEIPT_FIELD)} instead of this Stock Entry")
    if doc.get(PARTNER_RETURN_FIELD):
        from .partner_returns import guard_partner_return_stock_entry

        guard_partner_return_stock_entry(doc)
    if doc.get(OWNERSHIP_CONVERSION_FIELD):
        from .ownership_conversions import guard_conversion_stock_entry

        guard_conversion_stock_entry(doc)


def allow_linked_receipt_cancellation(doc: Any, method: str | None = None) -> None:
    """Keep traceability links while allowing a controlled cascade cancel."""
    del method
    import frappe

    receipt_cancel = doc.get(RECEIPT_FIELD) and getattr(
        frappe.flags,
        CANCELLATION_FLAG,
        False,
    )
    from .partner_returns import CANCELLATION_FLAG as PARTNER_RETURN_CANCELLATION_FLAG

    partner_return_cancel = doc.get(PARTNER_RETURN_FIELD) and getattr(
        frappe.flags,
        PARTNER_RETURN_CANCELLATION_FLAG,
        False,
    )
    from .ownership_conversions import CANCELLATION_FLAG as CONVERSION_CANCELLATION_FLAG

    conversion_cancel = doc.get(OWNERSHIP_CONVERSION_FIELD) and getattr(
        frappe.flags,
        CONVERSION_CANCELLATION_FLAG,
        False,
    )
    if not receipt_cancel and not partner_return_cancel and not conversion_cancel:
        return

    ignored_doctypes = set(doc.get("ignore_linked_doctypes") or ())
    if receipt_cancel:
        ignored_doctypes.update(RECEIPT_BACKLINK_DOCTYPES)
    if partner_return_cancel:
        ignored_doctypes.add("CC Partner Return")
    if conversion_cancel:
        ignored_doctypes.add("CC Ownership Conversion")
    doc.ignore_linked_doctypes = tuple(sorted(ignored_doctypes))
