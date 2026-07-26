from __future__ import annotations

from erpnext_ua.ecommerce.providers.base_provider import BaseProvider
from erpnext_ua.ecommerce.providers.shop_express.service import (
    pull_customers,
    pull_orders,
    push_catalog,
    push_order_statuses,
    push_stock,
    test_connection,
)


class ShopExpressProvider(BaseProvider):
    provider_code = "shop_express"

    def __init__(self, channel):
        self.channel = channel
        self.code = f"shop_express:{channel.name}"

    def is_enabled(self) -> bool:
        return int(self.channel.enabled or 0) == 1

    def test_connection(self) -> dict:
        return test_connection(self.channel)

    def sync_orders(self) -> dict:
        if self.channel.orders_transport != "API":
            return {"ok": True, "skipped": True, "reason": "orders transport is not API"}
        return pull_orders(self.channel)

    def sync_stock(self) -> dict:
        if self.channel.stock_transport != "API":
            return {"ok": True, "skipped": True, "reason": "stock transport is not API"}
        return push_stock(self.channel)

    def sync_customers(self) -> dict:
        if self.channel.customers_transport != "API":
            return {"ok": True, "skipped": True, "reason": "customers transport is not API"}
        return pull_customers(self.channel)

    def sync_catalog(self) -> dict:
        if self.channel.catalog_transport != "API":
            return {"ok": True, "skipped": True, "reason": "catalog transport is not API"}
        return push_catalog(self.channel)

    def sync_order_statuses(self) -> dict:
        if self.channel.order_status_transport != "API":
            return {"ok": True, "skipped": True, "reason": "order status transport is not API"}
        return push_order_statuses(self.channel)
