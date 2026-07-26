from __future__ import annotations

import hashlib

import frappe
from frappe.utils import get_datetime, now_datetime, time_diff_in_seconds


def dispatch() -> dict:
    """Queue due provider-instance jobs without duplicating active RQ jobs."""
    queued = []
    if not frappe.db.exists("DocType", "OcStore Settings"):
        return {"ok": True, "queued": queued}
    for name in frappe.get_all("OcStore Settings", filters={"enabled": 1}, pluck="name"):
        settings = frappe.get_doc("OcStore Settings", name)
        export_due = [
            row.entity
            for row in (settings.get("sync_entities") or [])
            if row.entity in {"Products", "Prices", "Stock", "Photos"}
            and int(row.enabled or 0)
            and row.method == "File"
            and _due(row)
        ]
        orders_due = any(
            _due(row)
            for row in (settings.get("sync_entities") or [])
            if row.entity == "Orders" and int(row.enabled or 0) and row.method == "File"
        )
        suffix = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
        if export_due:
            frappe.enqueue(
                "erpnext_ua.ecommerce.providers.ocstore.service.export_bundle",
                queue="long",
                timeout=3600,
                job_id=f"ocstore-export-{suffix}",
                deduplicate=True,
                settings_name=name,
                entities=export_due,
            )
            queued.append(f"{name}:export:{','.join(sorted(export_due))}")
        if orders_due:
            frappe.enqueue(
                "erpnext_ua.ecommerce.providers.ocstore.service.import_order_files",
                queue="long",
                timeout=3600,
                job_id=f"ocstore-orders-{suffix}",
                deduplicate=True,
                settings_name=name,
            )
            queued.append(f"{name}:orders")
    return {"ok": True, "queued": queued}


def _due(row) -> bool:
    interval = int(row.interval_minutes or 0)
    if interval <= 0:
        return False
    if not row.last_run_at:
        return True
    return time_diff_in_seconds(now_datetime(), get_datetime(row.last_run_at)) >= interval * 60
