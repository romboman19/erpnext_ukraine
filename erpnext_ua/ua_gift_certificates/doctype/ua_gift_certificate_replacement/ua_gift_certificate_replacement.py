import frappe
from frappe.model.document import Document


class UAGiftCertificateReplacement(Document):
    def validate(self):
        if (
            self.reason != "Return Restore"
            and self.requested_by
            and self.approved_by
            and self.requested_by == self.approved_by
        ):
            frappe.throw("Replacement requester and approver must be different users")
