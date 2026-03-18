from __future__ import annotations

import frappe
from frappe import _

from ukrainian_integrations.payments.privat_pos.gateway_client import PrivatPOSGatewayClient
from ukrainian_integrations.utils.logger import log_event


def _cfg(key: str, default=None):
    return frappe.conf.get(key, default)


def _pb_pos_settings() -> dict:
    if frappe.db.exists("DocType", "PB POS Settings"):
        try:
            d = frappe.get_single("PB POS Settings")
            return {
                "gateway_url": (d.get("gateway_url") or "").strip(),
                "api_key": (d.get_password("api_key") or "").strip(),
                "timeout": int(d.get("request_timeout_sec") or 20),
            }
        except Exception:
            pass
    return {
        "gateway_url": (_cfg("pb_pos_gateway_url") or "").strip(),
        "api_key": (_cfg("pb_pos_api_key") or "").strip(),
        "timeout": int(_cfg("pb_pos_timeout", 20) or 20),
    }


def _resolve_terminal(terminal: str) -> dict:
    if not terminal:
        frappe.throw(_("Terminal is required"))
    if not frappe.db.exists("DocType", "PB POS Terminal"):
        frappe.throw(_("DocType PB POS Terminal not found"))

    # terminal can be docname or terminal_name
    name = terminal
    if not frappe.db.exists("PB POS Terminal", name):
        name = frappe.db.get_value("PB POS Terminal", {"terminal_name": terminal}, "name")
        if not name:
            frappe.throw(_("PB POS Terminal not found: {0}").format(terminal))

    d = frappe.get_doc("PB POS Terminal", name)
    if int(d.get("is_active") or 0) != 1:
        frappe.throw(_("Terminal is inactive"))

    ip = (d.get("ip_address") or "").strip()
    if not ip:
        frappe.throw(_("Terminal IP is empty"))

    return {
        "name": d.name,
        "terminal_name": d.get("terminal_name") or d.name,
        "ip": ip,
        "port": int(d.get("tcp_port") or 2000),
    }


def _client() -> PrivatPOSGatewayClient:
    cfg = _pb_pos_settings()
    base_url = cfg.get("gateway_url")
    api_key = cfg.get("api_key")
    timeout = cfg.get("timeout", 20)
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


@frappe.whitelist()
def pb_pos_test_connection(terminal: str) -> dict:
    t = _resolve_terminal(terminal)
    out = _client().ping()
    log_event("privat_pos", "success", f"Terminal connection test OK {t['name']}", request_payload=t, response_payload=out)
    return {"ok": True, "terminal": t, "gateway": out}


@frappe.whitelist()
def pb_pos_test_payment(terminal: str, amount: float = 1.0) -> dict:
    t = _resolve_terminal(terminal)
    amt = float(amount or 0)
    if amt <= 0:
        frappe.throw(_("Amount must be > 0"))
    operation_id = f"TEST-SALE-{frappe.generate_hash(length=8)}"
    req = {"terminal": t, "amount": amt, "operation_id": operation_id}
    log_event("privat_pos", "queued", f"Test sale start {t['name']}", request_payload=req)
    res = _client().sale(terminal_ip=t['ip'], port=t['port'], amount=amt, operation_id=operation_id)
    log_event("privat_pos", "success", f"Test sale done {t['name']}", request_payload=req, response_payload=res)
    return {"ok": True, "terminal": t, "response": res, "operation_id": operation_id}


@frappe.whitelist()
def pb_pos_test_refund(terminal: str, amount: float = 1.0, reference_operation_id: str | None = None) -> dict:
    t = _resolve_terminal(terminal)
    amt = float(amount or 0)
    if amt <= 0:
        frappe.throw(_("Amount must be > 0"))
    operation_id = f"TEST-REFUND-{frappe.generate_hash(length=8)}"
    req = {"terminal": t, "amount": amt, "operation_id": operation_id, "reference_operation_id": reference_operation_id}
    log_event("privat_pos", "queued", f"Test refund start {t['name']}", request_payload=req)
    res = _client().refund(terminal_ip=t['ip'], port=t['port'], amount=amt, operation_id=operation_id, reference_operation_id=reference_operation_id)
    log_event("privat_pos", "success", f"Test refund done {t['name']}", request_payload=req, response_payload=res)
    return {"ok": True, "terminal": t, "response": res, "operation_id": operation_id}
