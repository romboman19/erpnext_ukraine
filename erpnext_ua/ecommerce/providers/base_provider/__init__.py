from __future__ import annotations

from erpnext_ua.ecommerce.core.contracts import EcommerceProvider


class BaseProvider(EcommerceProvider):
    code = "base"

    def is_enabled(self) -> bool:
        return False

    def sync_orders(self) -> dict:
        return {"ok": True, "skipped": True}

    def sync_stock(self) -> dict:
        return {"ok": True, "skipped": True}

    def sync_customers(self) -> dict:
        return {"ok": True, "skipped": True}

    def sync_catalog(self) -> dict:
        return {"ok": True, "skipped": True}

    def sync_order_statuses(self) -> dict:
        return {"ok": True, "skipped": True}
