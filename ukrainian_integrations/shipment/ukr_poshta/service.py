from __future__ import annotations

import frappe
from frappe import _

from ukrainian_integrations.shipment.ukr_poshta.api import UkrPoshtaClient
from ukrainian_integrations.utils.logger import log_event


def _cfg(key: str, default=None):
    return frappe.conf.get(key, default)


def get_client() -> UkrPoshtaClient:
    ecom = _cfg("ukrposhta_ecom_token")
    tracking = _cfg("ukrposhta_tracking_token")
    api_base = _cfg("ukrposhta_api_base", "https://www.ukrposhta.ua/ecom/0.0.1")
    if not ecom:
        frappe.throw(_("Не задано ukrposhta_ecom_token у site_config.json"))
    return UkrPoshtaClient(ecom_token=ecom, tracking_token=tracking, api_base=api_base)


@frappe.whitelist()
def track_barcode(barcode: str) -> dict:
    if not barcode:
        frappe.throw(_("Barcode is required"))
    try:
        row = get_client().track(barcode)
        log_event("ukr_poshta", "success", f"Track {barcode}", request_payload={"barcode": barcode}, response_payload=row)
        return {"ok": True, "barcode": barcode, "raw": row}
    except Exception:
        log_event("ukr_poshta", "error", f"Track failed {barcode}", request_payload={"barcode": barcode}, error_trace=frappe.get_traceback())
        raise


@frappe.whitelist()
def sync_sales_invoice_up_statuses(limit: int = 50) -> dict:
    docs = frappe.get_all(
        "Sales Invoice",
        filters={"up_barcode": ["is", "set"]},
        fields=["name", "up_barcode", "up_status"],
        order_by="modified desc",
        limit=max(1, min(int(limit or 50), 500)),
    )
    if not docs:
        return {"ok": True, "checked": 0, "updated": 0}

    client = get_client()
    updated = 0
    for d in docs:
        code = d.get("up_barcode")
        if not code:
            continue
        try:
            row = client.track(code)
            status = row.get("status") or row.get("eventName") or row.get("state") or ""
            if status and status != (d.get("up_status") or ""):
                frappe.db.set_value("Sales Invoice", d["name"], "up_status", status, update_modified=False)
                updated += 1
        except Exception:
            log_event(
                "ukr_poshta",
                "error",
                f"Sync failed for {d["name"]}",
                reference_doctype="Sales Invoice",
                reference_name=d["name"],
                request_payload={"barcode": code},
                error_trace=frappe.get_traceback(),
            )

    if updated:
        frappe.db.commit()
    return {"ok": True, "checked": len(docs), "updated": updated}
