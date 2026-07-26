import frappe
from frappe import _
from frappe.model.document import Document

from ukrainian_integrations.utils.validation import validate_http_url


class LiqPayProfile(Document):
    def validate(self):
        if self.result_url:
            validate_http_url(self.result_url, "LiqPay Result URL")
        if self.server_url:
            validate_http_url(self.server_url, "LiqPay Server URL")
        if int(self.enabled or 0) == 1 and (
            not self.public_key
            or not self.get_password("private_key", raise_exception=False)
            or not self.server_url
        ):
            frappe.throw(_("Enabled LiqPay profile requires public/private keys and Server URL"))
        if int(self.auto_create_payment_entry or 0) == 1 and (
            not self.bank_account or not self.mode_of_payment or not self.company
        ):
            frappe.throw(_("LiqPay auto reconciliation requires Company, Bank Account and Mode of Payment"))
        if self.bank_account and self.company:
            bank_company = frappe.db.get_value("Bank Account", self.bank_account, "company")
            if bank_company and bank_company != self.company:
                frappe.throw(_("LiqPay ERP Bank Account belongs to a different company"))
