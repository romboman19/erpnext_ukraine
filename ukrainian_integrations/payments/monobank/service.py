from __future__ import annotations

import frappe
from frappe import _

from ukrainian_integrations.payments.monobank.client import MonobankClient
from ukrainian_integrations.utils.logger import log_event


def _cfg(key: str, default=None):
    return frappe.conf.get(key, default)


def _client() -> MonobankClient:
    token = _cfg("monobank_token")
    if not token:
        frappe.throw(_("Не задано monobank_token у site_config.json"))
    return MonobankClient(token)


@frappe.whitelist()
def mono_create_invoice(sales_invoice: str, amount: float | None = None) -> dict:
    if not sales_invoice:
        frappe.throw(_("Sales Invoice is required"))

    si = frappe.get_doc("Sales Invoice", sales_invoice)
    amt = float(amount) if amount is not None else float(si.grand_total or 0)
    if amt <= 0:
        frappe.throw(_("Amount must be > 0"))

    amount_kopecks = int(round(amt * 100))
    order_ref = f"SI-{si.name}"
    merchant_info = {
        "reference": order_ref,
        "destination": f"Оплата рахунку {si.name}",
        "basketOrder": [{"name": si.name, "qty": 1, "sum": amount_kopecks}],
    }

    try:
        out = _client().create_invoice(
            amount_kopecks=amount_kopecks,
            merchant_paym_info=merchant_info,
            redirect_url=_cfg("monobank_redirect_url"),
            web_hook_url=_cfg("monobank_webhook_url"),
        )
        log_event(
            "monobank",
            "success",
            f"Invoice created for {si.name}",
            reference_doctype="Sales Invoice",
            reference_name=si.name,
            request_payload={"amount": amt, "amount_kopecks": amount_kopecks, "reference": order_ref},
            response_payload=out,
        )
        return {"ok": True, "sales_invoice": si.name, "response": out}
    except Exception:
        log_event(
            "monobank",
            "error",
            f"Invoice create failed for {si.name}",
            reference_doctype="Sales Invoice",
            reference_name=si.name,
            request_payload={"amount": amt, "amount_kopecks": amount_kopecks, "reference": order_ref},
            error_trace=frappe.get_traceback(),
        )
        raise


@frappe.whitelist(allow_guest=True)
def mono_webhook():
    payload = frappe.request.get_json(silent=True) or {}
    invoice_id = payload.get("invoiceId") or ""
    status = payload.get("status") or ""

    # idempotency guard by invoiceId
    if invoice_id and frappe.db.exists(
        "Hunter Integration Log",
        {"integration": "monobank", "status": "success", "message": ["like", f"%invoice:{invoice_id}%"]},
    ):
        return {"ok": True, "idempotent": True}

    log_event(
        "monobank",
        "success",
        f"Webhook status:{status} invoice:{invoice_id}",
        request_payload=payload,
    )
    return {"ok": True}
