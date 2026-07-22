from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document


class EcommerceOrderStatusMap(Document):
    def validate(self):
        self.channel_status = (self.channel_status or "").strip()
        if not self.channel_status:
            frappe.throw(_("Channel Status is required"))
        if self.erp_action not in {
            "Create Sales Order",
            "Create Sales Invoice",
            "Create SO+SI+Payment",
            "Update Status",
            "Ignore",
        }:
            frappe.throw(_("Unsupported ecommerce ERP action"))
        if int(self.reserve_stock or 0):
            if self.erp_action not in {"Create Sales Order", "Create SO+SI+Payment"}:
                frappe.throw(_("Stock reservation requires a Sales Order action"))
            self.reserve_days = int(self.reserve_days or 0)
            if not 1 <= self.reserve_days <= 365:
                frappe.throw(_("Reserve Days must be between 1 and 365"))
        else:
            self.reserve_days = 0
