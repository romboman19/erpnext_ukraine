from __future__ import annotations

import frappe
from frappe import _

from .api import NovaPoshtaClient


def _cfg(key: str, default=None):
    return frappe.conf.get(key, default)


def get_client() -> NovaPoshtaClient:
    api_key = _cfg("novaposhta_api_key")
    if not api_key:
        frappe.throw(_("Не задано novaposhta_api_key у site_config.json"))
    return NovaPoshtaClient(api_key)


@frappe.whitelist()
def track_ttn(ttn: str) -> dict:
    if not ttn:
        frappe.throw(_("TTN is required"))
    row = get_client().track(ttn)
    return {
        "ok": True,
        "ttn": ttn,
        "status": row.get("Status") or row.get("StatusCode") or "",
        "raw": row,
    }


@frappe.whitelist()
def sync_sales_invoice_ttn_statuses(limit: int = 50) -> dict:
    docs = frappe.get_all(
        "Sales Invoice",
        filters={"np_ttn_number": ["is", "set"]},
        fields=["name", "np_ttn_number", "np_status"],
        order_by="modified desc",
        limit=max(1, min(int(limit or 50), 500)),
    )
    if not docs:
        return {"ok": True, "checked": 0, "updated": 0}

    client = get_client()
    updated = 0
    for d in docs:
        ttn = d.get("np_ttn_number")
        if not ttn:
            continue
        try:
            row = client.track(ttn)
            status = row.get("Status") or row.get("StatusCode") or ""
            if status and status != (d.get("np_status") or ""):
                frappe.db.set_value("Sales Invoice", d["name"], "np_status", status, update_modified=False)
                updated += 1
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Nova Poshta sync failed for {d[name]}")

    if updated:
        frappe.db.commit()
    return {"ok": True, "checked": len(docs), "updated": updated}
