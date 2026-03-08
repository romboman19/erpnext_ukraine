from __future__ import annotations

import frappe

from ukrainian_integrations.ecommerce.providers.prom_ua.api import PromUAClient
from ukrainian_integrations.utils.logger import log_event


def _cfg(key: str, default=None):
    return frappe.conf.get(key, default)


def _client() -> PromUAClient:
    token = _cfg("prom_ua_token")
    base = _cfg("prom_ua_api_base", "https://my.prom.ua/api/v1")
    if not token:
        raise RuntimeError("prom_ua_token is not configured")
    return PromUAClient(token=token, base_url=base)


def pull_orders(limit: int = 50) -> dict:
    status_filter = _cfg("prom_ua_orders_status")
    req = {"limit": int(limit), "status": status_filter}
    try:
        data = _client().list_orders(status=status_filter, limit=limit, page=1)
        orders = data.get("orders") or data.get("data") or []
        created = 0
        skipped = 0

        for o in orders:
            ext_id = str(o.get("id") or o.get("order_id") or "").strip()
            if not ext_id:
                skipped += 1
                continue

            if frappe.db.exists("Sales Order", {"po_no": ext_id}):
                skipped += 1
                continue

            customer_name = o.get("client_first_name") or o.get("client") or "Prom Customer"
            if not frappe.db.exists("Customer", customer_name):
                c = frappe.get_doc({"doctype": "Customer", "customer_name": customer_name, "customer_group": "Commercial", "territory": "All Territories"})
                c.insert(ignore_permissions=True)

            so = frappe.new_doc("Sales Order")
            so.customer = customer_name
            so.po_no = ext_id
            so.delivery_date = frappe.utils.nowdate()

            items = o.get("products") or o.get("items") or []
            for it in items:
                code = it.get("sku") or it.get("external_id") or it.get("article")
                if not code:
                    continue
                if not frappe.db.exists("Item", code):
                    continue
                so.append("items", {
                    "item_code": code,
                    "qty": float(it.get("quantity") or 1),
                    "rate": float(it.get("price") or 0),
                })

            if not so.items:
                skipped += 1
                continue

            so.insert(ignore_permissions=True)
            created += 1

        if created:
            frappe.db.commit()

        out = {"ok": True, "received": len(orders), "created": created, "skipped": skipped}
        log_event("prom_ua", "success", "Orders synced", request_payload=req, response_payload=out)
        return out
    except Exception:
        log_event("prom_ua", "error", "Orders sync failed", request_payload=req, error_trace=frappe.get_traceback())
        raise


def push_stock(limit: int = 1000) -> dict:
    rows = frappe.db.sql(
        """
        select i.item_code as sku, ifnull(sum(b.actual_qty), 0) as qty
        from `tabItem` i
        left join `tabBin` b on b.item_code=i.item_code
        where i.disabled=0
        group by i.item_code
        limit %s
        """,
        (int(limit),),
        as_dict=True,
    )
    payload_rows = [{"sku": r.get("sku"), "quantity": int(float(r.get("qty") or 0))} for r in rows if r.get("sku")]

    if not payload_rows:
        return {"ok": True, "pushed": 0}

    try:
        res = _client().update_stock(payload_rows)
        out = {"ok": True, "pushed": len(payload_rows), "response": res}
        log_event("prom_ua", "success", "Stock pushed", request_payload={"count": len(payload_rows)}, response_payload={"ok": True})
        return out
    except Exception:
        log_event("prom_ua", "error", "Stock push failed", request_payload={"count": len(payload_rows)}, error_trace=frappe.get_traceback())
        raise
