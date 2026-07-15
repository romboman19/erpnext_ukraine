import frappe
from frappe import _
from frappe.model.document import Document


class MonobankProfile(Document):
    def validate(self):
        if int(self.enabled or 0) == 1 and not self.get_password("token", raise_exception=False):
            frappe.throw(_("Enabled Monobank profile requires a token"))
        if int(self.auto_import_enabled or 0) == 1 and (not self.account or not self.bank_account or not self.company):
            frappe.throw(_("Monobank auto import requires Account, ERP Bank Account and Company"))
        if int(self.auto_import_days_back or 0) < 1 or int(self.auto_import_days_back or 0) > 31:
            frappe.throw(_("Monobank auto import days must be between 1 and 31"))
        if self.bank_account and self.company:
            bank_company = frappe.db.get_value("Bank Account", self.bank_account, "company")
            if bank_company and bank_company != self.company:
                frappe.throw(_("Monobank ERP Bank Account belongs to a different company"))
