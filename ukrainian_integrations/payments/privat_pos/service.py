from __future__ import annotations

import frappe
from frappe import _

from ukrainian_integrations.payments.privat_pos.gateway_client import PrivatPOSGatewayClient
from ukrainian_integrations.utils.logger import log_event


def _cfg(key: str, default=None):
    return frappe.conf.get(key, default)


def _client() -> PrivatPOSGatewayClient:
    base_url = _cfg("pb_pos_gateway_url")
    api_key = _cfg("pb_pos_api_key")
    timeout = _cfg("pb_pos_timeout", 20)
    if not base_url:
        frappe.throw(_("Не задано pb_pos_gateway_url у site_config.json"))
    if not api_key:
        frappe.throw(_("Не задано pb_pos_api_key у site_config.json"))
    return PrivatPOSGatewayClient(base_url=base_url, api_key=api_key, timeout=timeout)


@frappe.whitelist()
def pb_pos_healthcheck() -> dict:
    try:
        out = _client().ping()
        log_event("privat_pos", "success", "Healthcheck OK", response_payload=out)
        return {"ok": True, "response": out}
    except Exception:
        log_event("privat_pos", "error", "Healthcheck failed", error_trace=frappe.get_traceback())
        raise


@frappe.whitelist()
def pb_pos_sale(sales_invoice: str, terminal_ip: str, amount: float | None = None, terminal_port: int = 2000) -> dict:
    if not sales_invoice:
        frappe.throw(_("Sales Invoice is required"))
    if not terminal_ip:
        frappe.throw(_("Terminal IP is required"))

    si = frappe.get_doc("Sales Invoice", sales_invoice)
    sale_amount = float(amount) if amount is not None else float(si.grand_total or 0)
    if sale_amount <= 0:
        frappe.throw(_("Сума оплати має бути більшою за 0"))

    operation_id = f"SI-{si.name}"
    payload = {
        "sales_invoice": si.name,
        "terminal_ip": terminal_ip,
        "terminal_port": int(terminal_port or 2000),
        "amount": sale_amount,
        "operation_id": operation_id,
    }

    log_event("privat_pos", "queued", f"Sale start for {si.name}", reference_doctype="Sales Invoice", reference_name=si.name, request_payload=payload)

    try:
        res = _client().sale(
            terminal_ip=terminal_ip,
            port=int(terminal_port or 2000),
            amount=sale_amount,
            operation_id=operation_id,
        )

        # Optional write-back if fields already exist in target ERP
        for field, value in {
            "pb_pos_status": res.get("status") or res.get("result") or "",
            "pb_pos_rrn": res.get("rrn") or "",
            "pb_pos_invoice_number": res.get("invoice_number") or "",
            "pb_pos_card_mask": res.get("card_mask") or "",
        }.items():
            if field in si.meta.get_valid_columns() and value:
                si.db_set(field, value, update_modified=False)

        log_event("privat_pos", "success", f"Sale done for {si.name}", reference_doctype="Sales Invoice", reference_name=si.name, request_payload=payload, response_payload=res)
        return {"ok": True, "sales_invoice": si.name, "response": res}
    except Exception:
        log_event("privat_pos", "error", f"Sale failed for {si.name}", reference_doctype="Sales Invoice", reference_name=si.name, request_payload=payload, error_trace=frappe.get_traceback())
        raise
