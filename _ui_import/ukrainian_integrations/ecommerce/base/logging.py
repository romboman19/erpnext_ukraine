from __future__ import annotations

import frappe

from ukrainian_integrations.utils.logger import sanitize_text


def append_sync_log(
    *,
    channel: str,
    entity: str,
    direction: str,
    method: str,
    status: str,
    idempotency_key: str,
    records_ok: int = 0,
    records_failed: int = 0,
    message: str = "",
    payload_ref: str = "",
):
    """Append one redacted terminal/ambiguous ecommerce run record."""
    doc = frappe.get_doc(
        {
            "doctype": "Ecommerce Sync Log",
            "channel": sanitize_text(channel)[:140],
            "entity": entity,
            "direction": direction,
            "method": method,
            "status": status,
            "idempotency_key": sanitize_text(idempotency_key)[:240],
            "records_ok": max(0, int(records_ok or 0)),
            "records_failed": max(0, int(records_failed or 0)),
            "message": sanitize_text(message)[:1000],
            "payload_ref": sanitize_text(payload_ref)[:1000],
        }
    )
    doc.insert(ignore_permissions=True)
    return doc
