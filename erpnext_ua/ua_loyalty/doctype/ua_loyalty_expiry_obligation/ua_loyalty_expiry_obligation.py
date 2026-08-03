import frappe
from frappe.model.document import Document


class UALoyaltyExpiryObligation(Document):
    def validate(self):
        if not getattr(frappe.flags, "ua_loyalty_service", False):
            frappe.throw("Expiry obligation змінюється лише через UA Loyalty service")

    def on_trash(self):
        if not frappe.flags.in_uninstall:
            frappe.throw("Expiry obligation не можна видалити")
