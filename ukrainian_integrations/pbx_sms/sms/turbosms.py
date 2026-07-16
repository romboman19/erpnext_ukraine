from __future__ import annotations

import re

import frappe
import requests
from frappe import _

from ukrainian_integrations.utils.logger import log_event, sanitize_payload, sanitize_text
from ukrainian_integrations.utils.operations import (
    canonical_hash,
    mark_operation,
    require_new_or_return_success,
    reserve_operation,
)
from ukrainian_integrations.utils.security import SALES_MANAGER_ROLES, SALES_ROLES, permitted_doc, require_roles

TURBOSMS_URL_DEFAULT = "https://api.turbosms.ua/message/send.json"


def _cfg(key: str, default=None):
    return frappe.conf.get(key, default)


def cint(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _normalize_phone(phone: str) -> str:
    p = "".join(ch for ch in (phone or "") if ch.isdigit() or ch == "+")
    if p.startswith("0"):
        p = "+38" + p
    if p.startswith("380"):
        p = "+" + p
    return p


def _get_turbosms_settings() -> dict:
    token = (_cfg("turbosms_token") or "").strip()
    base_url = (_cfg("turbosms_url") or TURBOSMS_URL_DEFAULT).strip()
    sender = (_cfg("turbosms_sender") or "").strip()
    senders = []

    if frappe.db.exists("DocType", "TurboSMS Settings"):
        s = frappe.get_single("TurboSMS Settings")
        if cint(s.get("enabled")) != 1:
            return {"enabled": 0, "base_url": base_url, "sender": sender, "senders": []}
        token = (s.get_password("token", raise_exception=False) or "").strip()
        base_url = (s.get("base_url") or "").strip()

        rows = s.get("senders") or []
        active_rows = [r for r in rows if cint(getattr(r, "is_active", 0)) == 1]
        senders = [
            {
                "sender_name": (r.get("sender_name") or "").strip(),
                "is_default": cint(r.get("is_default") or 0),
                "is_active": cint(r.get("is_active") or 0),
            }
            for r in active_rows
            if (r.get("sender_name") or "").strip()
        ]
        if senders:
            default_row = next((x for x in senders if x.get("is_default") == 1), None)
            sender = (default_row or senders[0]).get("sender_name") or ""
        else:
            sender = (s.get("sender") or "").strip()

    return {
        "enabled": 1,
        "token": token,
        "base_url": base_url or TURBOSMS_URL_DEFAULT,
        "sender": sender or "HUNTER RV",
        "senders": senders,
    }


def configured_sender_names(cfg: dict | None = None) -> list[str]:
    """Return the canonical local sender list shared by every ERPNext module."""
    if cfg is None:
        cfg = _get_turbosms_settings()
    names: list[str] = []
    seen: set[str] = set()
    for row in cfg.get("senders") or []:
        name = str(row.get("sender_name") or "").strip()
        key = name.casefold()
        if name and key not in seen:
            names.append(name)
            seen.add(key)

    # Keep an existing installation working while it migrates from the legacy
    # single sender field to the child table. It is still a locally configured
    # value, never a free-form value supplied by another module.
    legacy_default = str(cfg.get("sender") or "").strip()
    if not names and legacy_default:
        names.append(legacy_default)
    return names


def resolve_configured_sender(sender: str | None = None, cfg: dict | None = None) -> str:
    """Resolve default sender and reject values outside TurboSMS Settings."""
    if cfg is None:
        cfg = _get_turbosms_settings()
    names = configured_sender_names(cfg)
    default_sender = str(cfg.get("sender") or "").strip()
    resolved = str(sender or default_sender).strip()
    by_key = {name.casefold(): name for name in names}
    if not resolved or resolved.casefold() not in by_key:
        raise ValueError("Sender is not configured or inactive")
    return by_key[resolved.casefold()]




def _write_turbosms_log(*, status: str, phone: str, sender: str, message_text: str, response_json=None, error_text: str = ""):
    try:
        if not frappe.db.exists("DocType", "TurboSMS Log"):
            return
        stored_message = message_text
        if int(frappe.conf.get("turbosms_store_message_text", 0) or 0) != 1:
            stored_message = (
                f"[redacted sha256={canonical_hash({'text': message_text})} length={len(message_text or '')}]"
            )
        doc = frappe.get_doc({
            "doctype": "TurboSMS Log",
            "status": status,
            "phone": phone,
            "sender": sender,
            "message_text": stored_message,
            "response_json": (
                sanitize_text(frappe.as_json(sanitize_payload(response_json)))[:20000]
                if response_json is not None
                else ""
            ),
            "error_text": error_text or "",
        })
        doc.error_text = sanitize_text(doc.error_text)[:20000]
        doc.insert(ignore_permissions=True)
    except Exception:
        frappe.logger("ukrainian_integrations").error(
            {"where": "turbosms_log", "trace": sanitize_text(frappe.get_traceback())}
        )


def classify_send_response(data) -> tuple[str, list[str]]:
    if not isinstance(data, dict):
        return "unknown", []
    try:
        top_level_ok = int(data.get("response_code", -1)) == 0
    except (TypeError, ValueError):
        return "unknown", []
    response_status = str(data.get("response_status") or "").upper()
    if not top_level_ok:
        return "failed", []
    if not response_status:
        return "unknown", []
    if response_status != "OK":
        return "failed", []
    rows = data.get("response_result")
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return "unknown", []
    if "response_code" not in rows[0]:
        return "unknown", []
    try:
        row_ok = int(rows[0].get("response_code")) == 0
    except (TypeError, ValueError):
        return "unknown", []
    if not row_ok:
        return "failed", []
    message_id = rows[0].get("message_id")
    return ("succeeded", [str(message_id)]) if message_id else ("unknown", [])


def successful_message_ids(data) -> list[str]:
    status, message_ids = classify_send_response(data)
    return message_ids if status == "succeeded" else []


def _send_sms_internal(phone: str, text: str, idempotency_key: str, sender: str | None = None) -> dict:
    if not (idempotency_key or "").strip():
        frappe.throw(_("idempotency_key is required"))
    cfg = _get_turbosms_settings()
    if cint(cfg.get("enabled")) != 1:
        frappe.throw(_("TurboSMS integration is disabled"))
    token = (cfg.get("token") or "").strip()
    if not token:
        frappe.throw(_("Не задано токен TurboSMS (site_config.turbosms_token або TurboSMS Settings.token)"))

    to = _normalize_phone(phone)
    if not re.fullmatch(r"\+[1-9]\d{7,14}", to):
        frappe.throw(_("Некоректний номер телефону"))

    body = (text or "").strip()
    if not body:
        frappe.throw(_("Текст повідомлення обов'язковий"))
    max_text_length = max(1, min(int(frappe.conf.get("turbosms_max_text_length", 4096) or 4096), 10_000))
    if len(body) > max_text_length:
        frappe.throw(_("SMS text exceeds the configured maximum length ({0})").format(max_text_length))

    try:
        sender_name = resolve_configured_sender(sender, cfg)
    except ValueError as exc:
        frappe.throw(_(str(exc)), frappe.PermissionError)
    url = (cfg.get("base_url") or TURBOSMS_URL_DEFAULT).strip()

    reservation = reserve_operation(
        idempotency_key=f"turbosms:send:{idempotency_key}",
        integration="turbosms",
        operation_type="send_sms",
        request_payload={
            "phone": to,
            "sender": sender_name,
            "text_sha256": canonical_hash({"text": body}),
            "text_length": len(body),
        },
    )
    cached = require_new_or_return_success(reservation)
    if cached is not None:
        return cached

    payload = {
        "recipients": [to],
        "sms": {"sender": sender_name, "text": body},
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    log_event("turbosms", "queued", f"Send SMS to {to}", request_payload={"phone": to, "sender": sender_name})
    _write_turbosms_log(status="queued", phone=to, sender=sender_name, message_text=body)
    mark_operation(reservation.doc, "unknown", response_payload={"phase": "external_request_in_progress"})

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=25)
    except requests.RequestException:
        mark_operation(reservation.doc, "unknown", error=frappe.get_traceback())
        log_event("turbosms", "error", f"SMS send failed to {to}", request_payload={"phone": to, "sender": sender_name, "text_length": len(body)}, error_trace=frappe.get_traceback())
        _write_turbosms_log(status="error", phone=to, sender=sender_name, message_text=body, error_text=frappe.get_traceback())
        raise

    try:
        data = resp.json() if (resp.text or "").strip() else {}
    except ValueError:
        mark_operation(reservation.doc, "unknown", error="TurboSMS returned malformed JSON")
        frappe.throw(_("TurboSMS returned an invalid response"))

    safe_req = {"phone": to, "sender": sender_name, "text_length": len(body)}
    if resp.status_code >= 400:
        operation_status = "failed" if resp.status_code < 500 else "unknown"
        mark_operation(reservation.doc, operation_status, response_payload=data, error=f"HTTP {resp.status_code}")
        log_event("turbosms", "error", f"HTTP {resp.status_code}", request_payload=safe_req, response_payload=data)
        _write_turbosms_log(status="error", phone=to, sender=sender_name, message_text=body, response_json=data, error_text=f"HTTP {resp.status_code}")
        frappe.throw(_("TurboSMS помилка: HTTP {0}").format(resp.status_code))

    response_class, message_ids = classify_send_response(data)
    if response_class != "succeeded":
        provider_status = data.get("response_status") if isinstance(data, dict) else "invalid_response"
        mark_operation(reservation.doc, response_class, response_payload=data, error=str(provider_status))
        log_event(
            "turbosms",
            "error",
            "Provider rejected SMS" if response_class == "failed" else "Provider returned an ambiguous SMS response",
            request_payload=safe_req,
            response_payload=data,
        )
        _write_turbosms_log(status="error", phone=to, sender=sender_name, message_text=body, response_json=data, error_text=str(provider_status))
        if response_class == "failed":
            frappe.throw(_("TurboSMS rejected the message: {0}").format(provider_status))
        frappe.throw(_("TurboSMS returned an ambiguous response; reconcile before retrying"))

    message_id = message_ids[0]
    result = {"ok": True, "phone": to, "sender": sender_name, "message_id": message_id, "response": data}
    mark_operation(reservation.doc, "succeeded", external_id=message_id, response_payload=result)
    log_event("turbosms", "success", f"SMS sent to {to}", request_payload=safe_req, response_payload=data)
    _write_turbosms_log(status="success", phone=to, sender=sender_name, message_text=body, response_json=data)
    return result


@frappe.whitelist()
def get_sender_options() -> dict:
    require_roles(*SALES_ROLES)
    cfg = _get_turbosms_settings()
    return {
        "enabled": cint(cfg.get("enabled")) == 1,
        "senders": configured_sender_names(cfg),
        "default_sender": resolve_configured_sender(cfg=cfg) if configured_sender_names(cfg) else "",
    }


@frappe.whitelist()
def send_sms_from_settings(phone: str, text: str, idempotency_key: str, sender: str | None = None) -> dict:
    require_roles(*SALES_MANAGER_ROLES)
    return _send_sms_internal(phone=phone, text=text, sender=sender, idempotency_key=idempotency_key)


@frappe.whitelist()
def send_sms(phone: str, text: str, idempotency_key: str, sender: str | None = None) -> dict:
    require_roles(*SALES_ROLES)
    return _send_sms_internal(phone=phone, text=text, sender=sender, idempotency_key=idempotency_key)


@frappe.whitelist()
def send_sms_to_customer(customer: str, text: str, idempotency_key: str, sender: str | None = None) -> dict:
    require_roles(*SALES_ROLES)
    if not customer:
        frappe.throw(_("Customer is required"))
    c = permitted_doc("Customer", customer, "read")
    phone = c.get("mobile_no") or c.get("phone")
    if not phone:
        frappe.throw(_("У клієнта не заповнений телефон"))
    return _send_sms_internal(phone=phone, text=text, sender=sender, idempotency_key=idempotency_key)
