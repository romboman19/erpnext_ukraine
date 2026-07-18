from __future__ import annotations

from ukrainian_integrations.ecommerce.providers.base_provider import BaseProvider


class OcStoreProvider(BaseProvider):
    provider_code = "ocstore"

    def __init__(self, channel):
        self.channel = channel
        self.code = f"ocstore:{channel.name}"

    def is_enabled(self) -> bool:
        return int(self.channel.enabled or 0) == 1

    def sync_orders(self) -> dict:
        return {"ok": True, "skipped": True, "reason": "ocStore orders are imported from an exchange file"}

    def sync_stock(self) -> dict:
        return {"ok": True, "skipped": True, "reason": "ocStore stock is exported from Desk as XML"}

    def sync_customers(self) -> dict:
        return {"ok": True, "skipped": True, "reason": "ocStore customers are created with imported orders"}

    def sync_catalog(self) -> dict:
        return {"ok": True, "skipped": True, "reason": "ocStore catalog is exported from Desk as XML"}

    def sync_order_statuses(self) -> dict:
        return {"ok": True, "skipped": True, "reason": "ocStore order statuses use file exchange"}
