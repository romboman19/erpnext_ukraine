import frappe
from frappe.model.document import Document


class UAGiftCertificateProgram(Document):
    def validate(self):
        if self.currency != "UAH":
            frappe.throw("V1 supports UAH only")
        if self.accounting_model == "Deferred Discount" and not frappe.db.get_single_value(
            "UA Gift Certificate Settings", "deferred_discount_enabled"
        ):
            frappe.throw("Deferred Discount is disabled")
        if self.status == "Active" and self.accounting_model not in {"Prepaid Payment", "Promotional Only"}:
            frappe.throw("This accounting model is not production-supported in V1")
        if self.usage_policy == "Single Use No Change" and self.under_spend_policy == "Forfeit Remainder":
            if not frappe.db.get_single_value("UA Gift Certificate Settings", "allow_forfeit_remainder"):
                frappe.throw("Remainder forfeiture is disabled")
        if not self.is_new() and self.get_db_value("status") == "Active" and self.has_value_changed("policy_checksum"):
            frappe.throw("Active Program rules are immutable; create a new version")
