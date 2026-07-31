import frappe
from frappe.model.document import Document


class UALoyaltyBonusLot(Document):
    def validate(self):
        if not getattr(frappe.flags, "ua_loyalty_service", False):
            frappe.throw("Bonus lot змінюється лише через UA Loyalty service")

    def on_trash(self):
        if not frappe.flags.in_uninstall:
            frappe.throw("Bonus lot не можна видалити")
