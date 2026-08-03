from __future__ import annotations

import frappe

from erpnext_ua.ua_fiscal.outbox import ensure_sales_invoice_job, process_job

COMPLETED_RECEIPT_STATUSES = ("Fiscalized", "Offline")
PROTECTED_RECEIPT_STATUSES = (*COMPLETED_RECEIPT_STATUSES, "Uncertain")
RETRYABLE_STATUSES = ("Pending", "Error", "Uncertain")


def on_payment_submit(doc, method=None) -> None:
    """Fiscalize fully paid ecommerce invoices after payment confirmation."""
    settings = frappe.get_cached_doc("PRRO Settings")
    if not settings.enabled:
        return
    for sales_invoice in _ecommerce_invoices(doc):
        outstanding = frappe.db.get_value("Sales Invoice", sales_invoice, "outstanding_amount")
        if abs(frappe.utils.flt(outstanding)) > 0.01:
            continue
        _fiscalize_and_record(sales_invoice)


def recover_pending_ecommerce_receipts(limit: int = 100) -> dict:
    """Retry paid ecommerce invoices whose first fiscal attempt failed."""
    settings = frappe.get_cached_doc("PRRO Settings")
    if not settings.enabled:
        return {"checked": 0, "fiscalized": 0, "failed": 0}
    invoices = frappe.get_all(
        "Sales Invoice",
        filters={
            "docstatus": 1,
            "ua_ecommerce_channel": ("is", "set"),
            "ua_ecommerce_fiscal_status": ("in", RETRYABLE_STATUSES),
            "outstanding_amount": ("between", [-0.01, 0.01]),
        },
        order_by="ua_ecommerce_fiscal_updated_at asc, modified asc",
        limit=max(1, min(int(limit or 100), 500)),
        pluck="name",
    )
    fiscalized = 0
    for sales_invoice in invoices:
        if _fiscalize_and_record(sales_invoice):
            fiscalized += 1
    return {
        "checked": len(invoices),
        "fiscalized": fiscalized,
        "failed": len(invoices) - fiscalized,
    }


def before_payment_cancel(doc, method=None) -> None:
    """A fiscal receipt must be reversed with a return, not by deleting payment."""
    for sales_invoice in _ecommerce_invoices(doc):
        receipt = frappe.db.get_value(
            "PRRO Receipt",
            {
                "sales_invoice": sales_invoice,
                "status": ("in", PROTECTED_RECEIPT_STATUSES),
            },
            "name",
        )
        if receipt:
            frappe.throw(
                f"Payment Entry cannot be cancelled because ecommerce invoice {sales_invoice} "
                f"has fiscal receipt {receipt}. Create a return Sales Invoice and fiscal return receipt."
            )


def _ecommerce_invoices(payment_entry) -> list[str]:
    names = []
    for reference in payment_entry.get("references") or []:
        if reference.reference_doctype != "Sales Invoice" or not reference.reference_name:
            continue
        channel = frappe.db.get_value(
            "Sales Invoice",
            reference.reference_name,
            "ua_ecommerce_channel",
        )
        if channel:
            names.append(str(reference.reference_name))
    return list(dict.fromkeys(names))


def _fiscalize_and_record(sales_invoice: str) -> bool:
    _, receipt_status = _receipt_state(sales_invoice)
    if receipt_status in COMPLETED_RECEIPT_STATUSES:
        _set_fiscal_state(sales_invoice, receipt_status)
        return True
    if receipt_status == "Uncertain":
        _set_fiscal_state(sales_invoice, receipt_status)
        return False
    _set_fiscal_state(sales_invoice, "Pending")
    try:
        job = ensure_sales_invoice_job(sales_invoice)
        if not job:
            frappe.throw(f"Для ecommerce рахунку {sales_invoice} не налаштовано касу ПРРО")
        result = process_job(job.name)
        receipt = result.get("receipt")
        status = frappe.db.get_value("PRRO Receipt", receipt, "status") if receipt else None
        if not status:
            status = "Uncertain" if result.get("status") == "Uncertain" else "Error"
        error = "" if status in PROTECTED_RECEIPT_STATUSES else result.get("error_message") or ""
        _set_fiscal_state(sales_invoice, status, error)
        return status in COMPLETED_RECEIPT_STATUSES
    except Exception as exc:
        _, status = _receipt_state(sales_invoice)
        _set_fiscal_state(
            sales_invoice,
            status if status in PROTECTED_RECEIPT_STATUSES else "Error",
            "" if status in PROTECTED_RECEIPT_STATUSES else str(exc),
        )
        frappe.log_error(
            frappe.get_traceback(),
            f"PRRO ecommerce auto-fiscalize {sales_invoice}",
        )
        return False


def _receipt_state(sales_invoice: str) -> tuple[str | None, str | None]:
    receipt = frappe.db.get_value(
        "PRRO Receipt",
        {"sales_invoice": sales_invoice},
        ["name", "status"],
        order_by="creation desc",
        as_dict=True,
    )
    if not receipt:
        return None, None
    return str(receipt.name), str(receipt.status)


def _set_fiscal_state(sales_invoice: str, status: str, error: str = "") -> None:
    frappe.db.set_value(
        "Sales Invoice",
        sales_invoice,
        {
            "ua_ecommerce_fiscal_status": status,
            "ua_ecommerce_fiscal_error": str(error or "")[:1000],
            "ua_ecommerce_fiscal_updated_at": frappe.utils.now_datetime(),
        },
        update_modified=False,
    )
