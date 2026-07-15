from __future__ import annotations

import frappe

from ukrainian_integrations.ecommerce.providers.base_provider import BaseProvider
from ukrainian_integrations.ecommerce.providers.prom_ua.service import pull_orders, push_stock


class PromUAProvider(BaseProvider):
    code = "prom_ua"

    def is_enabled(self) -> bool:
        return int(frappe.conf.get("prom_ua_enabled", 0) or 0) == 1

    def sync_orders(self) -> dict:
        limit = int(frappe.conf.get("prom_ua_orders_limit", 50) or 50)
        return pull_orders(limit=limit)

    def sync_stock(self) -> dict:
        limit = int(
            frappe.conf.get(
                "prom_ua_stock_max_items",
                frappe.conf.get("prom_ua_stock_limit", 100_000),
            )
            or 100_000
        )
        return push_stock(limit=limit)
