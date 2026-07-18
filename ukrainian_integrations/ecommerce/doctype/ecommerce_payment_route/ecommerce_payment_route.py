from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document


class EcommercePaymentRoute(Document):
    def validate(self):
        self.channel_payment_type = (self.channel_payment_type or "").strip()
        if not self.channel_payment_type:
            frappe.throw(_("Channel Payment Type is required"))
        if self.paid_to_account and frappe.db.get_value("Account", self.paid_to_account, "is_group"):
            frappe.throw(_("Paid To Account must be a ledger account"))
