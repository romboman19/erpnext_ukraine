from __future__ import annotations

import frappe

from ukrainian_integrations.ecommerce.providers.base_provider import BaseProvider


class PromUAProvider(BaseProvider):
    code = "prom_ua"

    def is_enabled(self) -> bool:
        return int(frappe.conf.get("prom_ua_enabled", 0) or 0) == 1

    def sync_orders(self) -> dict:
        # TODO: implement real API pull and SO creation
        return {"ok": True, "provider": self.code, "received": 0, "created": 0}

    def sync_stock(self) -> dict:
        # TODO: implement stock push
        return {"ok": True, "provider": self.code, "pushed": 0}
