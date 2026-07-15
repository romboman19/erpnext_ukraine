from __future__ import annotations

import json
import re
from datetime import timedelta

import frappe

SENSITIVE_KEYS = {
    "api_key",
    "api_token",
    "apikey",
    "authorization",
    "card",
    "card_number",
    "cvv",
    "ecom_token",
    "private_key",
    "signature",
    "token",
    "tracking_token",
    "counterparty_token",
    "webhook_key",
}

SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer|basic)?\s*)[^\s,;\]}]+"),
    re.compile(r"(?i)([?&](?:token|api_key|apikey|signature)=)[^&#\s]+"),
    re.compile(r"(?i)(/(?:apiKey|token)/)[^/\s]+"),
    re.compile(
        r'''(?i)(["'](?:token|api_key|api_token|apikey|private_key|signature|webhook_key)["']\s*:\s*["'])[^"']+'''
    ),
)


def sanitize_text(value: str | None) -> str:
    text = str(value or "")
    for pattern in SENSITIVE_TEXT_PATTERNS:
        text = pattern.sub(r"\1***REDACTED***", text)
    return text


def sanitize_payload(value, *, key: str | None = None):
    if key and key.lower() in SENSITIVE_KEYS:
        return "***REDACTED***"
    if isinstance(value, dict):
        return {str(k): sanitize_payload(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_payload(v) for v in value]
    if isinstance(value, tuple):
        return [sanitize_payload(v) for v in value]
    return value


def log_event(
    integration: str,
    status: str,
    message: str = "",
    *,
    direction: str = "out",
    reference_doctype: str | None = None,
    reference_name: str | None = None,
    request_payload: dict | list | str | None = None,
    response_payload: dict | list | str | None = None,
    error_trace: str | None = None,
):
    def _dump(v):
        if v is None:
            return ""
        sanitized = sanitize_payload(v)
        if isinstance(sanitized, dict | list):
            out = json.dumps(sanitized, ensure_ascii=False, default=str)
        else:
            out = str(sanitized)
        out = sanitize_text(out)
        limit = int(frappe.conf.get("ua_integration_log_max_chars", 20000) or 20000)
        return out[: max(1000, limit)]

    try:
        doc = frappe.get_doc(
            {
                "doctype": "Hunter Integration Log",
                "integration": integration,
                "direction": direction,
                "status": status,
                "reference_doctype": reference_doctype,
                "reference_name": reference_name,
                "message": sanitize_text(message)[:1000],
                "request_payload": _dump(request_payload),
                "response_payload": _dump(response_payload),
                "error_trace": _dump(error_trace),
            }
        )
        doc.insert(ignore_permissions=True)
    except Exception:
        frappe.logger("ukrainian_integrations").error(
            {
                "integration": integration,
                "status": status,
                "message": sanitize_text(message),
                "trace": sanitize_text(frappe.get_traceback()),
            }
        )


def purge_old_logs() -> dict:
    """Bound DB log growth. Runs as a scheduler job and keeps a configurable audit window."""
    retention_days = max(30, int(frappe.conf.get("ua_integration_log_retention_days", 180) or 180))
    cutoff = frappe.utils.now_datetime() - timedelta(days=retention_days)
    deleted = 0
    for doctype in ("Hunter Integration Log", "TurboSMS Log"):
        if not frappe.db.exists("DocType", doctype):
            continue
        names = frappe.get_all(doctype, filters={"creation": ["<", cutoff]}, pluck="name", limit_page_length=5000)
        for name in names:
            frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)
        deleted += len(names)

    call_log_retention_days = max(30, int(frappe.conf.get("vitalpbx_call_log_retention_days", 365) or 365))
    if frappe.db.exists("DocType", "VitalPBX Call Log"):
        call_cutoff = frappe.utils.now_datetime() - timedelta(days=call_log_retention_days)
        names = frappe.get_all(
            "VitalPBX Call Log",
            filters={"creation": ["<", call_cutoff]},
            pluck="name",
            limit_page_length=5000,
        )
        for name in names:
            frappe.delete_doc("VitalPBX Call Log", name, ignore_permissions=True, force=True)
        deleted += len(names)
    return {
        "ok": True,
        "deleted": deleted,
        "retention_days": retention_days,
        "vitalpbx_call_log_retention_days": call_log_retention_days,
    }
