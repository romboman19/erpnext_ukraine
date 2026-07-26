from __future__ import annotations

import frappe

from erpnext_ua.ecommerce.core.orchestrator import (
    sync_catalog_all,
    sync_customers_all,
    sync_order_statuses_all,
    sync_orders_all,
    sync_stock_all,
)


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


def cron_sync_customers():
    result = sync_customers_all()
    if not result.get("ok"):
        frappe.db.commit()
        raise RuntimeError("One or more ecommerce customer sync providers failed")
    return result


def cron_sync_catalog():
    result = sync_catalog_all()
    if not result.get("ok"):
        frappe.db.commit()
        raise RuntimeError("One or more ecommerce catalog sync providers failed")
    return result


def cron_sync_order_statuses():
    result = sync_order_statuses_all()
    if not result.get("ok"):
        frappe.db.commit()
        raise RuntimeError("One or more ecommerce order status sync providers failed")
    return result
