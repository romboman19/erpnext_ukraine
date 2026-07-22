from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

from ukrainian_integrations.utils.operations import canonical_hash


class EcommerceCustomerMapping(Document):
    def validate(self):
        self.external_customer_id = (self.external_customer_id or "").strip()
        identity = self.external_customer_id or (self.identity_hash or "").strip()
        if not identity:
            frappe.throw(_("External Customer ID or Identity Hash is required"))
        self.mapping_key = canonical_hash({"channel": self.channel, "identity": identity})
