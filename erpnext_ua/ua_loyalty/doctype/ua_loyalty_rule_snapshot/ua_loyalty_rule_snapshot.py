import frappe
from frappe.model.document import Document


class UALoyaltyRuleSnapshot(Document):
    def validate(self):
        if not self.is_new() and not getattr(frappe.flags, "ua_loyalty_service", False):
            frappe.throw("Опублікований snapshot є незмінним")

    def on_trash(self):
        if not frappe.flags.in_uninstall:
            frappe.throw("Опублікований snapshot не можна видалити")
