from __future__ import annotations

import base64
import json
import frappe
from frappe import _

from ukrainian_integrations.payments.liqpay.client import LiqPayClient
from ukrainian_integrations.utils.logger import log_event


def _cfg(key: str, default=None):
    return frappe.conf.get(key, default)


def _liqpay_settings() -> dict:
    if not frappe.db.exists("DocType", "LiqPay Settings"):
        return {}
    try:
        d = frappe.get_single("LiqPay Settings")
        return {
            "enabled": int(d.get("enabled") or 0),
            "public_key": (d.get("public_key") or "").strip(),
            "private_key": (d.get_password("private_key") or "").strip(),
            "result_url": (d.get("result_url") or "").strip(),
            "server_url": (d.get("server_url") or "").strip(),
        }
    except Exception:
        return {}


def _client() -> LiqPayClient:
    st = _liqpay_settings()
    pub = st.get("public_key") or _cfg("liqpay_public_key")
    prv = st.get("private_key") or _cfg("liqpay_private_key")
    if not pub or not prv:
        frappe.throw(_("Не задано liqpay_public_key / liqpay_private_key у site_config.json"))
    return LiqPayClient(pub, prv)


@frappe.whitelist()
def liqpay_initiate(sales_invoice: str, amount: float | None = None, result_url: str | None = None, server_url: str | None = None) -> dict:
    if not sales_invoice:
        frappe.throw(_("Sales Invoice is required"))
    si = frappe.get_doc("Sales Invoice", sales_invoice)
    amt = float(amount) if amount is not None else float(si.grand_total or 0)
    if amt <= 0:
        frappe.throw(_("Amount must be > 0"))

    client = _client()
    order_id = f"SI-{si.name}"
    payload = {
        "version": "3",
        "public_key": client.public_key,
        "action": "pay",
        "amount": amt,
        "currency": "UAH",
        "description": f"Оплата рахунку {si.name}",
        "order_id": order_id,
        "result_url": result_url or _liqpay_settings().get("result_url") or _cfg("liqpay_result_url"),
        "server_url": server_url or _liqpay_settings().get("server_url") or _cfg("liqpay_server_url"),
    }
    form = client.cnb_form_payload(payload)
    log_event("liqpay", "queued", f"Initiate {si.name}", reference_doctype="Sales Invoice", reference_name=si.name, request_payload=payload)
    return {"ok": True, "sales_invoice": si.name, "order_id": order_id, "data": form["data"], "signature": form["signature"]}


@frappe.whitelist(allow_guest=True)
def liqpay_callback(data: str | None = None, signature: str | None = None):
    if not data or not signature:
        frappe.local.response["http_status_code"] = 400
        return {"ok": False, "error": "missing_data_or_signature"}

    client = _client()
    expected = client.make_signature(data)
    if expected != signature:
        frappe.local.response["http_status_code"] = 401
        log_event("liqpay", "error", "Invalid callback signature")
        return {"ok": False, "error": "invalid_signature"}

    decoded = json.loads(base64.b64decode(data).decode("utf-8"))
    order_id = decoded.get("order_id") or ""
    status = decoded.get("status") or ""

    # idempotency: do not duplicate same tx callback
    tx_id = decoded.get("transaction_id") or decoded.get("liqpay_order_id") or ""
    if tx_id and frappe.db.exists("Hunter Integration Log", {"integration": "liqpay", "status": "success", "message": ["like", f"%tx:{tx_id}%"]}):
        return {"ok": True, "idempotent": True}

    ref = None
    if order_id.startswith("SI-"):
        ref = order_id[3:]

    log_event(
        "liqpay",
        "success",
        f"Callback status:{status} tx:{tx_id}",
        reference_doctype="Sales Invoice" if ref else None,
        reference_name=ref,
        request_payload=decoded,
    )
    return {"ok": True}
