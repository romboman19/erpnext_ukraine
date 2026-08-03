import frappe
from frappe.model.document import Document

from erpnext_ua.ua_gift_certificates.domain.money import money


class UAGiftCertificateBatch(Document):
    def validate(self):
        if int(self.quantity or 0) <= 0:
            frappe.throw("Batch quantity must be positive")
        if money(self.face_value) <= 0:
            frappe.throw("Batch face value must be positive")
        if self.sale_price in (None, ""):
            self.sale_price = self.face_value
        if not self.token_key_version:
            self.token_key_version = frappe.db.get_single_value(
                "UA Gift Certificate Settings", "token_hmac_key_version"
            ) or "v1"
        if not self.issue_date:
            self.issue_date = frappe.utils.today()
