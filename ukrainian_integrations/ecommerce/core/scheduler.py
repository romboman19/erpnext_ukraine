from __future__ import annotations

import frappe

from ukrainian_integrations.ecommerce.core.orchestrator import sync_orders_all, sync_stock_all


def cron_sync_orders():
    result = sync_orders_all()
    if not result.get("ok"):
        # Per-order savepoints preserve valid imports; commit that progress before
        # failing the job so one bad marketplace order cannot starve all others.
        frappe.db.commit()
        raise RuntimeError("One or more ecommerce order sync providers failed")
    return result


def cron_sync_stock():
    result = sync_stock_all()
    if not result.get("ok"):
        frappe.db.commit()
        raise RuntimeError("One or more ecommerce stock sync providers failed")
    return result
