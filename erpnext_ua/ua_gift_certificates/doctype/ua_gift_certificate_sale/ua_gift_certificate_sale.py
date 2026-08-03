import frappe
from frappe.model.document import Document


class UAGiftCertificateSale(Document):
    def validate(self):
        if self.docstatus and self.has_value_changed("idempotency_key"):
            frappe.throw("Submitted certificate sale is immutable")

    def on_cancel(self):
        frappe.throw("Use the controlled gift certificate refund service")
