import frappe
from frappe.model.document import Document


class UAGiftCertificateNetwork(Document):
    def validate(self):
        if self.currency != "UAH":
            frappe.throw("V1 supports UAH only")
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            frappe.throw("Valid To must not precede Valid From")

    def on_trash(self):
        if frappe.db.exists("UA Gift Certificate", {"network": self.name}):
            frappe.throw("A network with issued certificates cannot be deleted")
