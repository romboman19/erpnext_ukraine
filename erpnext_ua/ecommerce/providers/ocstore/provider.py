from __future__ import annotations

import frappe

from erpnext_ua.ecommerce.base.channel import AbstractEcommerceChannel
from erpnext_ua.ecommerce.base.serializers import get_serializer
from erpnext_ua.ecommerce.base.transport import FileDeliveryTransport
from erpnext_ua.ecommerce.providers.ocstore.service import export_bundle, import_order_files


class OcStoreProvider(AbstractEcommerceChannel):
    settings_doctype = "OcStore Settings"
    provider_code = "ocstore"

    def __init__(self, settings):
        super().__init__(settings)
        self.channel = settings
        self.code = self.channel_code

    def is_enabled(self) -> bool:
        return self.settings.doctype == self.settings_doctype and int(self.settings.enabled or 0) == 1

    def export_products(self, items=None) -> dict:
        del items
        return export_bundle(self.settings.name, entities=["Products"])

    def export_prices(self, items=None) -> dict:
        del items
        return export_bundle(self.settings.name, entities=["Prices"])

    def export_stock(self, items=None) -> dict:
        del items
        return export_bundle(self.settings.name, entities=["Stock"])

    def export_photos(self, items=None) -> dict:
        del items
        return export_bundle(self.settings.name, entities=["Photos"])

    def import_orders(self) -> dict:
        return import_order_files(self.settings.name)

    def import_customers(self) -> dict:
        return {"ok": True, "skipped": True, "reason": "Customers are imported with orders"}

    def resolve_transport(self, entity: str, direction: str):
        del direction
        endpoint = self.settings.photo_ftp_profile if entity == "Photos" else self.settings.ftp_profile
        return FileDeliveryTransport(frappe.get_doc("File Delivery Endpoint", endpoint))

    def resolve_serializer(self, entity: str, direction: str):
        del direction
        rows = [
            row
            for row in (self.settings.get("sync_entities") or [])
            if row.entity == entity and int(row.enabled or 0)
        ]
        if len(rows) != 1:
            raise ValueError(f"Exactly one enabled ocStore entity configuration is required: {entity}")
        return get_serializer(rows[0].file_format)

    def sync_orders(self) -> dict:
        return self.import_orders()

    def sync_stock(self) -> dict:
        return self.export_stock()

    def sync_customers(self) -> dict:
        return self.import_customers()

    def sync_catalog(self) -> dict:
        return self.export_products()

    def sync_order_statuses(self) -> dict:
        return {"ok": True, "skipped": True, "reason": "ocStore order statuses use file exchange"}

    def test_connection(self) -> dict:
        return self.resolve_transport("Products", "Export").test_connection()
