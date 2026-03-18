from __future__ import annotations

import requests
import frappe
from frappe import _

from ukrainian_integrations.utils.logger import log_event

TURBOSMS_URL_DEFAULT = "https://api.turbosms.ua/message/send.json"


def _cfg(key: str, default=None):
    return frappe.conf.get(key, default)


def cint(v):
    try:
        return int(v or 0)
    except Exception:
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
        try:
            s = frappe.get_single("TurboSMS Settings")
            token_doc = (s.get_password("token") or "").strip()
            base_url_doc = (s.get("base_url") or "").strip()
            if token_doc:
                token = token_doc
            if base_url_doc:
                base_url = base_url_doc

            rows = s.get("senders") or []
            active_rows = [r for r in rows if cint(getattr(r, "is_active", 0)) == 1] or rows
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
                sender = (default_row or senders[0]).get("sender_name") or sender
            elif s.get("sender"):
                sender = (s.get("sender") or "").strip() or sender
        except Exception:
            pass

    return {
        "token": token,
        "base_url": base_url or TURBOSMS_URL_DEFAULT,
        "sender": sender or "HUNTER RV",
        "senders": senders,
    }


def _send_sms_internal(phone: str, text: str, sender: str | None = None) -> dict:
    cfg = _get_turbosms_settings()
    token = (cfg.get("token") or "").strip()
    if not token:
        frappe.throw(_("Не задано токен TurboSMS (site_config.turbosms_token або TurboSMS Settings.token)"))

    to = _normalize_phone(phone)
    if not to:
        frappe.throw(_("Некоректний номер телефону"))

    body = (text or "").strip()
    if not body:
        frappe.throw(_("Текст повідомлення обов'язковий"))

    sender_name = (sender or cfg.get("sender") or "HUNTER RV").strip()
    url = (cfg.get("base_url") or TURBOSMS_URL_DEFAULT).strip()

    payload = {
        "recipients": [to],
        "sms": {"sender": sender_name, "text": body},
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    log_event("turbosms", "queued", f"Send SMS to {to}", request_payload={"phone": to, "sender": sender_name})

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=25)
        data = resp.json() if (resp.text or "").strip() else {}
        if resp.status_code >= 400:
            log_event(
                "turbosms",
                "error",
                f"HTTP {resp.status_code}",
                request_payload=payload,
                response_payload=data or {"text": (resp.text or "")[:1000]},
            )
            frappe.throw(_("TurboSMS помилка: HTTP {0}").format(resp.status_code))

        log_event("turbosms", "success", f"SMS sent to {to}", request_payload=payload, response_payload=data)
        return {"ok": True, "phone": to, "sender": sender_name, "response": data}
    except Exception:
        log_event("turbosms", "error", f"SMS send failed to {to}", request_payload=payload, error_trace=frappe.get_traceback())
        raise


@frappe.whitelist()
def get_sender_options() -> dict:
    cfg = _get_turbosms_settings()
    return {
        "senders": [x.get("sender_name") for x in cfg.get("senders", []) if x.get("sender_name")],
        "default_sender": cfg.get("sender") or "",
    }


@frappe.whitelist()
def send_sms_from_settings(phone: str, text: str, sender: str | None = None) -> dict:
    return _send_sms_internal(phone=phone, text=text, sender=sender)


@frappe.whitelist()
def send_sms(phone: str, text: str, sender: str | None = None) -> dict:
    return _send_sms_internal(phone=phone, text=text, sender=sender)


@frappe.whitelist()
def send_sms_to_customer(customer: str, text: str, sender: str | None = None) -> dict:
    if not customer:
        frappe.throw(_("Customer is required"))
    c = frappe.get_doc("Customer", customer)
    phone = c.get("mobile_no") or c.get("phone")
    if not phone:
        frappe.throw(_("У клієнта не заповнений телефон"))
    return _send_sms_internal(phone=phone, text=text, sender=sender)
