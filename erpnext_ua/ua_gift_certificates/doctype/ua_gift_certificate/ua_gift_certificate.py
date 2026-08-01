import frappe
from frappe.model.document import Document

from erpnext_ua.ua_gift_certificates.constants import WRITE_FLAG
from erpnext_ua.ua_gift_certificates.domain.money import ZERO, money


class UAGiftCertificate(Document):
    def validate(self):
        if not getattr(frappe.flags, WRITE_FLAG, False):
            frappe.throw("Gift Certificates are changed only through domain services")
        if self.currency != "UAH":
            frappe.throw("V1 supports UAH only")
        for fieldname in (
            "face_value",
            "sale_price",
            "current_balance",
            "paid_balance",
            "promotional_balance",
            "reserved_balance",
            "available_balance",
        ):
            if money(self.get(fieldname)) < ZERO:
                frappe.throw(f"{fieldname} cannot be negative")
        if money(self.current_balance) != money(self.paid_balance) + money(self.promotional_balance):
            frappe.throw("Current balance must equal paid plus promotional balance")
        expected_available = (
            ZERO
            if self.status in {"Blocked", "Expired", "Cancelled", "Refunded", "Replaced"}
            else max(money(self.current_balance) - money(self.reserved_balance), ZERO)
        )
        if money(self.available_balance) != expected_available:
            frappe.throw("Available balance cache is inconsistent")

    def on_trash(self):
        if not frappe.flags.in_uninstall:
            frappe.throw("Issued Gift Certificates cannot be deleted")
