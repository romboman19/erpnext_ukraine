from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

from ukrainian_integrations.utils.operations import canonical_hash


class EcommerceItemMapping(Document):
    def validate(self):
        self.external_sku = (self.external_sku or "").strip()
        self.external_id = (self.external_id or "").strip()
        if not self.external_sku:
            frappe.throw(_("External SKU is required"))
        self.mapping_key = canonical_hash({"channel": self.channel, "item": self.item})
        self.external_mapping_key = canonical_hash(
            {"channel": self.channel, "external_sku": self.external_sku}
        )
