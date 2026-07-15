import frappe
from frappe.model.document import Document


class CustomerIdentificationRequest(Document):
    def on_trash(self):
        frappe.throw(
            "Customer identification requests are audit records and cannot be deleted"
        )
