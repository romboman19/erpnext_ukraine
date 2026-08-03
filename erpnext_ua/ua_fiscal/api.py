from __future__ import annotations

import frappe

from erpnext_ua.ua_fiscal import orchestration
from erpnext_ua.ua_fiscal.authorization import (
    default_register_key,
    require_document_permission,
    require_receipt_reconciliation,
    require_register_control,
    require_roles,
    require_sales_invoice_fiscalization,
)
from erpnext_ua.ua_fiscal.sales_invoice import fiscalize_invoice


@frappe.whitelist(methods=["POST"])
def fiscalize_sales_invoice(sales_invoice: str) -> str | None:
    invoice = frappe.get_doc("Sales Invoice", sales_invoice)
    require_sales_invoice_fiscalization(invoice)
    return fiscalize_invoice(invoice.name)


@frappe.whitelist(methods=["POST"])
def register_device(cash_register: str, forced: bool = False) -> dict:
    register = frappe.get_doc("PRRO Cash Register", cash_register)
    require_register_control(register)
    forced = bool(frappe.utils.cint(forced))
    if forced:
        require_roles(("System Manager",))
    return orchestration.register_device(
        register.name,
        default_register_key(register),
        forced=forced,
    )


@frappe.whitelist(methods=["POST"])
def sync_register_state(cash_register: str) -> dict:
    register = frappe.get_doc("PRRO Cash Register", cash_register)
    require_register_control(register)
    return orchestration.sync_register_state(
        register.name,
        default_register_key(register),
    )


@frappe.whitelist(methods=["POST"])
def reconcile_receipt(receipt_name: str) -> dict:
    receipt = frappe.get_doc("PRRO Receipt", receipt_name)
    register = frappe.get_doc("PRRO Cash Register", receipt.cash_register)
    require_receipt_reconciliation(receipt, register)
    return orchestration.reconcile_receipt(receipt.name)


@frappe.whitelist(methods=["POST"])
def retry_fiscalization_job(job_name: str) -> dict:
    require_roles(("System Manager", "Accounts Manager"))
    job = frappe.get_doc("PRRO Fiscalization Job", job_name)
    require_document_permission(job, "read")
    if job.status == "Completed":
        return job.as_dict()
    frappe.db.set_value(
        "PRRO Fiscalization Job",
        job.name,
        {
            "status": "Pending",
            "attempt_count": 0,
            "next_attempt_at": None,
            "error_message": None,
        },
        update_modified=False,
    )
    from erpnext_ua.ua_fiscal.outbox import process_job

    return process_job(job.name)
